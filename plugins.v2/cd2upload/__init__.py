import os
import shutil
import threading
import time
from datetime import datetime, timedelta
from typing import List, Tuple, Dict, Any, Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler

try:
    from clouddrive import CloudDriveClient, Client
    from clouddrive.proto import CloudDrive_pb2
    CLOUDDRIVE_AVAILABLE = True
except ImportError:
    CloudDriveClient = None
    Client = None
    CloudDrive_pb2 = None
    CLOUDDRIVE_AVAILABLE = False

from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import eventmanager, Event
from app.core.meta import MetaBase
from app.core.metainfo import MetaInfo
from app.db import get_db
from app.db.models.transferhistory import TransferHistory
from app.db.subscribe_oper import SubscribeOper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import TransferInfo, Notification, WebhookEventInfo
from app.schemas.types import EventType, MediaType, NotificationType

lock = threading.Lock()


class Cd2Upload(_PluginBase):
    # 插件名称
    plugin_name = "CloudDrive2智能上传"
    # 插件描述
    plugin_desc = "智能上传媒体文件到CloudDrive2，支持上传监控、任务管理、事件驱动的STRM生成"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/thsrite/MoviePilot-Plugins/main/icons/clouddrive.png"
    # 插件版本
    plugin_version = "2.0.0"
    # 插件作者
    plugin_author = "honue & enhanced"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "cd2upload_"
    # 加载顺序
    plugin_order = 1
    # 可使用的用户级别
    auth_level = 1

    _enable = True
    _cron = '20'
    _onlyonce = False
    _cleanlink = False
    _monitor_upload = True
    _notify_upload = False
    _upload_retry_count = 3
    _cd2_confs = None
    _strm_plugin_name = None

    # 链接前缀
    _softlink_prefix_path = '/strm/'
    # cd2挂载本地媒体库前缀
    _cd_mount_prefix_path = '/CloudNAS/115/emby/'

    _scheduler = None
    _cd2_clients = {}
    _clients = {}
    _cd2_url = {}

    _subscribe_oper = SubscribeOper()

    def init_plugin(self, config: dict = None):
        # 检查版本兼容性
        try:
            if hasattr(settings, 'VERSION_FLAG'):
                version = settings.VERSION_FLAG  # V2
                logger.info("检测到MoviePilot V2版本")
            else:
                version = "v1"
                logger.info("检测到MoviePilot V1版本")
        except Exception as e:
            logger.warning(f"版本检测失败: {e}")
            version = "unknown"

        # 检查 clouddrive 依赖是否可用
        if not CLOUDDRIVE_AVAILABLE:
            logger.error("CloudDrive2智能上传启动失败：缺少 clouddrive 依赖库")
            logger.error("请安装依赖：pip install clouddrive")
            self.systemmessage.put("CloudDrive2智能上传启动失败：缺少 clouddrive 依赖库，请安装：pip install clouddrive")
            return

        if config:
            self._enable = config.get('enable', False)
            self._cron: int = int(config.get('cron', '20'))
            self._onlyonce = config.get('onlyonce', False)
            self._cleanlink = config.get('cleanlink', False)
            self._monitor_upload = config.get('monitor_upload', True)
            self._notify_upload = config.get('notify_upload', False)
            self._upload_retry_count = config.get('upload_retry_count', 3)
            self._cd2_confs = config.get('cd2_confs', '')
            self._strm_plugin_name = config.get('strm_plugin_name', '')
            self._softlink_prefix_path = config.get('softlink_prefix_path', '/strm/')
            self._cd_mount_prefix_path = config.get('cd_mount_prefix_path', '/CloudNAS/CloudDrive/115/emby/')

        self.stop_service()

        if not self._enable:
            return

        # 初始化CloudDrive2客户端
        self._cd2_clients = {}
        self._clients = {}
        self._cd2_url = {}
        
        if self._cd2_confs:
            self._setup_cd2_clients()

        # 补全历史文件
        file_num = int(os.getenv('FULL_RECENT', '0')) if os.getenv('FULL_RECENT', '0').isdigit() else 0
        if file_num:
            recent_files = [transfer_history.dest for transfer_history in
                            TransferHistory.list_by_page(count=file_num, db=get_db())]
            logger.info(f"补全 {len(recent_files)} 个历史文件")
            with lock:
                waiting_process_list = self.get_data('waiting_process_list') or []
                waiting_process_list = waiting_process_list + recent_files
                self.save_data('waiting_process_list', waiting_process_list)

        # 初始化调度器
        self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        if self._onlyonce:
            self._scheduler.add_job(func=self.task, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=10),
                                    name="CloudDrive2智能上传")
            logger.info("CloudDrive2智能上传，立即运行一次")

        if self._cleanlink:
            self._scheduler.add_job(func=self.clean, kwargs={"cleanlink": True}, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                    name="清理无效软链接")

        # 定期清理任务
        self._scheduler.add_job(func=self.clean, kwargs={"cleanlink": False}, trigger='interval', 
                                minutes=20, name="定期清理检查")

        # 上传监控任务（如果启用）
        if self._monitor_upload and self._cd2_clients:
            self._scheduler.add_job(func=self.monitor_upload_tasks, trigger='interval',
                                    minutes=10, name="上传任务监控")

        if self._scheduler.get_jobs():
            self._scheduler.print_jobs()
            self._scheduler.start()

        # 更新配置
        self.update_config({
            'enable': self._enable,
            'cron': self._cron,
            'onlyonce': False,
            'cleanlink': False,
            'monitor_upload': self._monitor_upload,
            'notify_upload': self._notify_upload,
            'upload_retry_count': self._upload_retry_count,
            'cd2_confs': self._cd2_confs,
            'strm_plugin_name': self._strm_plugin_name,
            'softlink_prefix_path': self._softlink_prefix_path,
            'cd_mount_prefix_path': self._cd_mount_prefix_path
        })

    @eventmanager.register(EventType.TransferComplete)
    def update_waiting_list(self, event: Event):
        transfer_info: TransferInfo = event.event_data.get('transferinfo', {})
        if not transfer_info.file_list_new:
            return
        with lock:
            # 等待转移的文件的链接的完整路径
            waiting_process_list = self.get_data('waiting_process_list') or []
            waiting_process_list = waiting_process_list + transfer_info.file_list_new
            self.save_data('waiting_process_list', waiting_process_list)

        logger.info(f'新入库，加入待转移列表 {transfer_info.file_list_new}')

        # 判断段转移任务开始时间 新剧晚点上传 老剧立马上传
        media_info: MediaInfo = event.event_data.get('mediainfo', {})
        meta: MetaBase = event.event_data.get("meta")

        if media_info:
            is_exist = self._subscribe_oper.exists(tmdbid=media_info.tmdb_id, doubanid=media_info.douban_id,
                                                   season=media_info.season)
            if is_exist:
                if not self._scheduler.get_jobs():
                    logger.info(f'追更剧集,{self._cron}分钟后开始执行任务...')
                try:
                    self._scheduler.remove_all_jobs()
                    self._scheduler.add_job(func=self.task, trigger='date',
                                            kwargs={"media_info": media_info, "meta": meta},
                                            run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(
                                                minutes=self._cron),
                                            name="cd2转移")
                except Exception as err:
                    logger.error(f"定时任务配置错误：{str(err)}")
            else:
                if not self._scheduler.get_jobs():
                    logger.info(f'已完结剧集,立即执行上传任务...')
                self._scheduler.remove_all_jobs()
                self._scheduler.add_job(func=self.task, trigger='date',
                                        run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=5),
                                        name="cd2转移")
            self._scheduler.start()

    def task(self, media_info: MediaInfo = None, meta: MetaBase = None):
        start_time = time.time()
        with (lock):
            waiting_process_list = self.get_data('waiting_process_list') or []
            processed_list = self.get_data('processed_list') or []

            if not waiting_process_list:
                logger.info('没有需要转移的媒体文件')
                return
            logger.info('strm文件将在源文件被清理后生成 软链接符号将被替换 strm和链接符号只会存在一个')
            logger.info(f'开始执行上传任务 {waiting_process_list} ')
            process_list = waiting_process_list.copy()
            total_num = len(waiting_process_list)
            for softlink_source in waiting_process_list:
                # 链接目录前缀 替换为 cd2挂载前缀
                cd2_dest = softlink_source.replace(self._softlink_prefix_path, self._cd_mount_prefix_path)
                if self._upload_file(softlink_source=softlink_source, cd2_dest=cd2_dest):
                    process_list.remove(softlink_source)
                    processed_list.append(softlink_source)
                    logger.info(f'【{total_num - len(process_list)}/{total_num}】 上传成功 {softlink_source} {cd2_dest}')
                else:
                    logger.error(f'上传失败 {softlink_source} {cd2_dest}')
                    continue
            logger.info("上传完毕，STRM文件将在链接文件失效后生成")
            self.save_data('waiting_process_list', process_list)
            self.save_data('processed_list', processed_list)
            end_time = time.time()

            favor: Dict = self.get_data('favor') or {}
            tmdb_id = str(media_info.tmdb_id)

            if media_info and favor.get(tmdb_id) and media_info.type == MediaType.TV:
                self.chain.post_message(Notification(
                    mtype=NotificationType.Plugin,
                    title=f"{media_info.title_year} {meta.episodes}",
                    text=f"上传成功 用时{int(end_time - start_time)}秒",
                    image=media_info.get_message_image()))

    def _upload_file(self, softlink_source: str = None, cd2_dest: str = None) -> bool:
        logger.info('')
        try:
            cd2_dest_folder, cd2_dest_file_name = os.path.split(cd2_dest)

            if not os.path.exists(cd2_dest_folder):
                os.makedirs(cd2_dest_folder)
                logger.info(f'创建文件夹 {cd2_dest_folder}')

            real_source = os.readlink(softlink_source)
            logger.info(f'源文件路径 {real_source}')

            if not os.path.exists(cd2_dest):
                # 将文件上传到当前文件夹 同步
                shutil.copy2(softlink_source, cd2_dest, follow_symlinks=True)
            else:
                logger.info(f'{cd2_dest_file_name} 已存在 {cd2_dest}')
            return True
        except Exception as e:
            logger.error(e)
            return False

    def clean(self, cleanlink: bool = False):
        with lock:
            waiting_process_list = self.get_data('processed_list') or []
            processed_list = waiting_process_list.copy()
            logger.info(f"已处理列表：{processed_list}")
            logger.debug(f"cleanlink {cleanlink}")

            for file in waiting_process_list:
                if not os.path.islink(file):
                    processed_list.remove(file)
                    logger.info(f"软链接符号不存在 {file}")
                    continue
                if cleanlink and os.path.islink(file):
                    try:
                        target_file = os.readlink(file)
                        os.remove(target_file)
                        logger.info(f"清除源文件 {target_file}")
                    except FileNotFoundError:
                        logger.warning(f"无法删除 {file} 指向的目标文件，目标文件不存在")
                    except OSError as e:
                        logger.error(f"删除 {file} 目标文件失败: {e}")

                if os.path.islink(file) and not os.path.exists(file):
                    os.remove(file)
                    processed_list.remove(file)
                    logger.info(f"删除本地链接文件 {file}")

                    # 构造 CloudDrive2 目标路径和STRM文件路径
                    cd2_dest = file.replace(self._softlink_prefix_path, self._cd_mount_prefix_path)
                    strm_file_path = os.path.splitext(file)[0] + '.strm'

                    # 发送STRM生成事件而不是直接生成文件
                    file_info = {
                        "softlink_path": file,
                        "cd2_path": cd2_dest,
                        "strm_path": strm_file_path
                    }
                    
                    # 如果配置了STRM插件，发送事件；否则直接生成
                    if self._strm_plugin_name:
                        self._send_strm_generation_event(file_info)
                    else:
                        # 保持原有的直接生成STRM文件逻辑作为备用
                        try:
                            with open(strm_file_path, "w") as strm_file:
                                strm_file.write(cd2_dest)
                            logger.info(f"直接生成STRM文件：{cd2_dest} -> {strm_file_path}")
                        except OSError as e:
                            logger.error(f"生成STRM文件失败: {e}")

                else:
                    logger.debug(f"{file} 未失效，跳过")

            self.save_data('processed_list', processed_list)

    def _setup_cd2_clients(self):
        """设置CloudDrive2客户端"""
        if not self._cd2_confs:
            return
            
        for cd2_conf in self._cd2_confs.split("\n"):
            if not cd2_conf.strip():
                continue
            try:
                parts = cd2_conf.strip().split("#")
                if len(parts) != 4:
                    logger.error(f"CloudDrive2配置格式错误：{cd2_conf}")
                    continue
                    
                cd2_name, cd2_url, username, password = parts
                _cd2_client = CloudDriveClient(cd2_url, username, password)
                _client = Client(cd2_url, username, password)
                
                if _cd2_client and _client:
                    self._cd2_clients[cd2_name] = _cd2_client
                    self._clients[cd2_name] = _client
                    self._cd2_url[cd2_name] = cd2_url
                    logger.info(f"CloudDrive2客户端连接成功：{cd2_name}")
                else:
                    logger.error(f"CloudDrive2客户端连接失败：{cd2_name}")
            except Exception as e:
                logger.error(f"设置CloudDrive2客户端失败：{e}")

    def monitor_upload_tasks(self):
        """监控上传任务状态"""
        if not self._cd2_clients:
            return
            
        for cd2_name, cd2_client in self._cd2_clients.items():
            try:
                # 获取上传任务列表
                upload_tasklist = cd2_client.upload_tasklist.list(page=0, page_size=20, filter="")
                if not upload_tasklist:
                    continue
                
                failed_tasks = []
                for task in upload_tasklist:
                    if task.get("status") == "FatalError":
                        failed_tasks.append({
                            "name": task.get("name", "未知文件"),
                            "error": task.get("errorMessage", "未知错误"),
                            "cd2_name": cd2_name
                        })
                
                if failed_tasks and self._notify_upload:
                    self._notify_upload_failures(failed_tasks)
                    
            except Exception as e:
                logger.error(f"监控{cd2_name}上传任务失败：{e}")

    def _notify_upload_failures(self, failed_tasks: List[Dict]):
        """通知上传失败任务"""
        if not failed_tasks:
            return
            
        title = f"CloudDrive2上传失败通知"
        error_details = []
        for task in failed_tasks:
            error_details.append(f"【{task['cd2_name']}】{task['name']}: {task['error']}")
        
        text = f"发现{len(failed_tasks)}个上传失败任务：\n" + "\n".join(error_details)
        
        self.post_message(
            title=title,
            text=text,
            mtype=NotificationType.Plugin
        )

    def _send_strm_generation_event(self, file_info: Dict):
        """发送STRM生成事件"""
        if not self._strm_plugin_name:
            logger.info(f"未配置STRM插件名称，跳过事件发送")
            return
            
        # 构造事件数据
        event_data = {
            "plugin_name": self.plugin_name,
            "action": "generate_strm",
            "file_path": file_info.get("softlink_path"),
            "cd2_path": file_info.get("cd2_path"),
            "strm_path": file_info.get("strm_path"),
            "target_plugin": self._strm_plugin_name
        }
        
        # 发送自定义事件
        event = Event(EventType.PluginAction, event_data)
        eventmanager.send_event(event)
        
        logger.info(f"已发送STRM生成事件到插件：{self._strm_plugin_name}")

    @eventmanager.register(EventType.WebhookMessage)
    def record_favor(self, event: Event):
        """
        记录favor剧集
        event='item.rate' channel='emby' item_type='TV' item_name='幽游白书' item_id=None item_path='/media/series/日韩剧/幽游白书 (2023)' season_id=None episode_id=None tmdb_id='121659' overview='该剧改编自富坚义博的同名漫画。讲述叛逆少年浦饭幽助（北村匠海 饰）为了救小孩不幸车祸身亡，没想到因此获得重生机会并成为灵界侦探，展开一段不可思议的人生。' percentage=None ip=None device_name=None client=None user_name='honue' image_url=None item_favorite=None save_reason=None item_isvirtual=None media_type='Series'
        """
        event_info: WebhookEventInfo = event.event_data
        # 只处理剧集喜爱
        if event_info.event != "item.rate" or event_info.item_type != "TV":
            return
        if event_info.channel != "emby":
            logger.info("目前只支持Emby服务端")
            return
        title = event_info.item_name
        tmdb_id = event_info.tmdb_id
        if title.count(" S"):
            logger.info("只处理喜爱整季，单集喜爱不处理")
            return
        try:
            meta = MetaInfo(title)
            mediainfo: MediaInfo = self.chain.recognize_media(meta=meta, tmdbid=tmdb_id, mtype=MediaType.TV)
            # 存储历史记录
            favor: Dict = self.get_data('favor') or {}
            if favor.get(tmdb_id):
                favor.pop(tmdb_id)
                logger.info(f"{mediainfo.title_year} 取消更新通知")
                self.chain.post_message(Notification(
                    mtype=NotificationType.Plugin,
                    title=f"{mediainfo.title_year} 取消更新通知", text=None, image=mediainfo.get_message_image()))
            else:
                favor[tmdb_id] = {
                    "title": title,
                    "type": mediainfo.type.value,
                    "year": mediainfo.year,
                    "poster": mediainfo.get_poster_image(),
                    "overview": mediainfo.overview,
                    "tmdbid": mediainfo.tmdb_id,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                logger.info(f"{mediainfo.title_year} 加入更新通知")
                self.chain.post_message(Notification(
                    mtype=NotificationType.Plugin,
                    title=f"{mediainfo.title_year} 加入更新通知", text=None, image=mediainfo.get_message_image()))
            self.save_data('favor', favor)
        except Exception as e:
            logger.error(str(e))

    def get_state(self) -> bool:
        return self._enable

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enable',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            }, {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'cleanlink',
                                            'label': '立即清理生成',
                                        }
                                    }
                                ]
                            }, {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '追更剧集入库（分钟）后上传',
                                            'placeholder': '20'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'softlink_prefix_path',
                                            'label': '本地链接媒体库路径前缀',
                                            'placeholder': '/strm/'
                                        }
                                    }
                                ]
                            }, {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cd_mount_prefix_path',
                                            'label': 'cd2挂载媒体库路径前缀',
                                            'placeholder': '/CloudNAS/115/emby/'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'monitor_upload',
                                            'label': '监控上传任务',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify_upload',
                                            'label': '上传失败通知',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'upload_retry_count',
                                            'label': '上传重试次数',
                                            'placeholder': '3'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'strm_plugin_name',
                                            'label': 'STRM生成插件名称',
                                            'placeholder': '留空则直接生成'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'cd2_confs',
                                            'label': 'CloudDrive2配置',
                                            'rows': 3,
                                            'placeholder': 'cd2配置1#http://127.0.0.1:19798#admin#123456\\n（一行一个配置）'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '本插件整合了CloudDrive2智能上传、任务监控、事件驱动STRM生成等功能，支持多CD2实例管理。'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            'enable': self._enable,
            'cron': self._cron,
            'onlyonce': self._onlyonce,
            'cleanlink': self._cleanlink,
            'monitor_upload': self._monitor_upload,
            'notify_upload': self._notify_upload,
            'upload_retry_count': self._upload_retry_count,
            'cd2_confs': self._cd2_confs,
            'strm_plugin_name': self._strm_plugin_name,
            'softlink_prefix_path': self._softlink_prefix_path,
            'cd_mount_prefix_path': self._cd_mount_prefix_path
        }

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))
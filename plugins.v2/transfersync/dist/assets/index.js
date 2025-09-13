// TransferSync Plugin Main Entry
(function() {
    'use strict';

    // 确保DOM加载完成
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    function init() {
        console.log('TransferSync Plugin loaded');

        // 如果在iframe中，则不需要额外初始化
        if (window.self !== window.top) {
            return;
        }

        // 设置全局样式
        injectGlobalStyles();
    }

    function injectGlobalStyles() {
        const style = document.createElement('style');
        style.textContent = `
            /* TransferSync Global Styles */
            .transfersync-container {
                max-width: 1200px;
                margin: 0 auto;
                padding: 20px;
            }

            .transfersync-card {
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                margin-bottom: 20px;
            }

            .transfersync-btn {
                background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                cursor: pointer;
                transition: transform 0.2s ease;
            }

            .transfersync-btn:hover {
                transform: translateY(-1px);
            }
        `;
        document.head.appendChild(style);
    }

    // 暴露一些有用的函数到全局
    window.TransferSyncUtils = {
        showNotification: function(message, type = 'info') {
            console.log(`[TransferSync ${type.toUpperCase()}]: ${message}`);
            // 这里可以集成实际的通知系统
        },

        formatPath: function(path) {
            return path.replace(/\\/g, '/');
        },

        validatePath: function(path) {
            return path && path.trim().length > 0;
        }
    };
})();
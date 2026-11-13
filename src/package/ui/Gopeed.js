/*
 * DSM 7 desktop integration for Gopeed.
 *
 * Since DSM 7.2 the old `"type": "legacy"` trick no longer decides whether an
 * app opens inside the DSM shell, so the app is declared as an Ext application
 * (INFO: dsmappname="SYNO.SDS.Gopeed.Application") and the window renders our
 * own iframe. Gopeed keeps running on its own port; nothing is proxied through
 * webman.
 */
Ext.namespace("SYNO.SDS.Gopeed.Utils");

Ext.apply(SYNO.SDS.Gopeed.Utils, function () {
    return {
        port: "__PORT__",

        url: function () {
            var proto = window.location.protocol === "https:" ? "https:" : "http:";
            return proto + "//" + window.location.hostname + ":" + this.port + "/";
        },

        // Gopeed only speaks HTTP. If DSM itself is served over https the
        // iframe would be blocked as mixed content, so explain that instead of
        // showing a blank window.
        getMainHtml: function () {
            var host = window.location.hostname;
            if (window.location.protocol === "https:") {
                return '' +
                    '<div style="padding:28px;font:14px/1.8 -apple-system,\'Segoe UI\',Arial,sans-serif;color:#333">' +
                    '<h3 style="margin:0 0 10px">Gopeed 无法在当前 HTTPS 的 DSM 中内嵌显示</h3>' +
                    '<p style="margin:0 0 14px">Gopeed 只提供 HTTP 服务，而当前 DSM 通过 HTTPS 打开，' +
                    '浏览器会以「混合内容」为由拦截嵌入。</p>' +
                    '<p style="margin:0 0 6px">两个解决办法：</p>' +
                    '<ol style="margin:0 0 14px;padding-left:22px">' +
                    '<li>改用 <a href="http://' + host + ':5000" target="_blank" rel="noopener">http://' + host +
                    ':5000</a> 打开 DSM，再点图标即可；</li>' +
                    '<li>或用「控制面板 → 登录门户 → 高级 → 反向代理」把 DSM 的 HTTPS 端口代理到 ' +
                    '<code>http://localhost:' + this.port + '</code>。</li>' +
                    '</ol>' +
                    '<p><a href="http://' + host + ':' + this.port + '" target="_blank" rel="noopener">' +
                    '在新窗口打开 Gopeed（HTTP）</a></p>' +
                    '</div>';
            }
            return '<iframe src="' + this.url() + '" title="Gopeed" ' +
                'allow="clipboard-read; clipboard-write; fullscreen" ' +
                'style="width:100%;height:100%;border:0;margin:0;display:block"></iframe>';
        }
    };
}());

Ext.define("SYNO.SDS.Gopeed.Application", {
    extend: "SYNO.SDS.AppInstance",
    appWindowName: "SYNO.SDS.Gopeed.MainWindow",
    constructor: function () {
        this.callParent(arguments);
    }
});

Ext.define("SYNO.SDS.Gopeed.MainWindow", {
    extend: "SYNO.SDS.AppWindow",
    constructor: function (a) {
        var MY = SYNO.SDS.Gopeed;
        this.appInstance = a.appInstance;
        MY.MainWindow.superclass.constructor.call(this, Ext.apply({
            layout: "fit",
            resizable: true,
            cls: "syno-my-win",
            maximizable: true,
            minimizable: true,
            width: 1200,
            height: 800,
            html: MY.Utils.getMainHtml()
        }, a));
        MY.Utils.ApplicationWindow = this;
    },

    onOpen: function () {
        SYNO.SDS.Gopeed.MainWindow.superclass.onOpen.apply(this, arguments);
    },

    onRequest: function (a) {
        SYNO.SDS.Gopeed.MainWindow.superclass.onRequest.call(this, a);
    },

    onClose: function () {
        SYNO.SDS.Gopeed.MainWindow.superclass.onClose.apply(this, arguments);
        this.doClose();
        return true;
    }
});

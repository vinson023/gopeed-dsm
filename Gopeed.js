/*
 * DSM 7 desktop integration for Gopeed.
 *
 * IMPORTANT: DSM serves its desktop UI with
 *     Content-Security-Policy: ... frame-src 'self' ...
 * so an iframe pointing at http://nas:38099 (a different origin) is blocked by
 * the browser - the window stays empty no matter what. There is no way to relax
 * that from a package (the header comes from DSM itself), and Gopeed cannot be
 * mounted under a sub-path either (its web UI and REST calls use absolute "/"
 * paths).
 *
 * Therefore the window shows a small launch page: one click opens Gopeed in a
 * standalone window without browser chrome, which behaves like an app window
 * rather than a new tab.
 */
Ext.namespace("SYNO.SDS.Gopeed.Utils");

Ext.apply(SYNO.SDS.Gopeed.Utils, function () {
    return {
        port: "__PORT__",

        url: function () {
            var proto = window.location.protocol === "https:" ? "https:" : "http:";
            return proto + "//" + window.location.hostname + ":" + this.port + "/";
        },

        notice: function () {
            if (window.location.protocol === "https:") {
                return "当前通过 HTTPS 访问 DSM。Gopeed 使用 DSM 的默认证书，如果它还是群晖自签证书，" +
                    "独立窗口里浏览器会提示证书风险（可直接继续访问）；也可以改用 " +
                    "<b>http://" + window.location.hostname + ":5000</b> 打开 DSM。";
            }
            return "";
        },

        launch: function () {
            window.open(this.url(), "gopeed_app",
                "width=1280,height=860,menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes");
        },

        component: function () {
            var self = this;
            var https = window.location.protocol === "https:";
            var note = self.notice();

            return {
                xtype: "container",
                layout: { type: "vbox", align: "center", pack: "center" },
                style: "background:#f7f8fa",
                items: [
                    {
                        xtype: "component",
                        html: '' +
                            '<div style="text-align:center;max-width:520px;font:14px/1.8 ' +
                            '-apple-system,\'Segoe UI\',\'PingFang SC\',\'Microsoft YaHei\',sans-serif;color:#333">' +
                            '<img src="images/icon_64.png" width="64" height="64" alt="Gopeed" ' +
                            'style="display:block;margin:0 auto 16px">' +
                            '<h2 style="margin:0 0 12px;font-size:20px;font-weight:600">Gopeed</h2>' +
                            '<p style="margin:0 0 8px;color:#555">DSM 的安全策略不允许在桌面窗口内嵌入其他端口的页面，' +
                            '所以 Gopeed 会在一个独立窗口里打开。</p>' +
                            (note ? '<p style="margin:0;color:#8a6d3b;background:#fcf8e3;border:1px solid #faebcc;' +
                                    'border-radius:4px;padding:10px 12px">' + note + '</p>' : '') +
                            '</div>'
                    },
                    {
                        xtype: "button",
                        text: "打开 Gopeed",
                        scale: "medium",
                        margin: "20 0 0 0",
                        handler: function () {
                            self.launch();
                        }
                    }
                ]
            };
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
            width: 640,
            height: 420,
            items: [MY.Utils.component()]
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

/*
 * DSM 7 desktop integration for Gopeed.
 *
 * DSM 7.2+ no longer decides "open in a new tab vs. embed" from
 * `"type": "legacy"` in config, so the app is declared as an Ext application
 * (INFO: dsmappname="SYNO.SDS.Gopeed.Application") and this file renders the
 * Gopeed UI inside the DSM window.
 *
 * Two things matter here:
 *  - the iframe must be a real Ext element (autoEl), otherwise the container
 *    collapses to 0 height and the window looks empty;
 *  - Gopeed only speaks HTTP. Over https DSM the iframe would be blocked as
 *    mixed content (http) or by an untrusted certificate (https), so we probe
 *    first and explain instead of showing a blank window.
 */
Ext.namespace("SYNO.SDS.Gopeed.Utils");

Ext.apply(SYNO.SDS.Gopeed.Utils, function () {
    var IFRAME_STYLE = "width:100%;height:100%;border:0;margin:0;display:block;background:#fff";

    return {
        port: "__PORT__",

        url: function () {
            var proto = window.location.protocol === "https:" ? "https:" : "http:";
            return proto + "//" + window.location.hostname + ":" + this.port + "/";
        },

        iframeHtml: function () {
            return '<iframe src="' + this.url() + '" title="Gopeed" style="' + IFRAME_STYLE + '" ' +
                'allow="clipboard-read; clipboard-write; fullscreen"></iframe>';
        },

        noticeHtml: function (reason) {
            var host = window.location.hostname;
            var title, body;

            if (reason === "probing") {
                title = "正在检测 Gopeed 是否可用…";
                body = '<p style="margin:0">马上就好。</p>';
            } else if (reason === "tls") {
                title = "无法在 HTTPS 页面里嵌入 Gopeed";
                body = '<p style="margin:0 0 14px">浏览器拒绝加载 <code>https://' + host + ':' + this.port +
                    '</code>。Gopeed 使用的是 DSM 的默认证书，如果它还是群晖自签证书，' +
                    'iframe 里不会出现"继续访问"的选项，只会一片空白。</p>' +
                    '<p style="margin:0 0 6px">任选一种：</p>' +
                    '<ol style="margin:0 0 14px;padding-left:22px">' +
                    '<li>把受信任的证书（如 Let\'s Encrypt）在「控制面板 → 安全性 → 证书」里设为默认，再重装本套件；</li>' +
                    '<li>用 <a href="http://' + host + ':5000" target="_blank" rel="noopener">http://' + host +
                    ':5000</a> 打开 DSM，主菜单里的 Gopeed 会直接嵌入显示；</li>' +
                    '<li>或直接访问 <a href="http://' + host + ':' + this.port + '" target="_blank" rel="noopener">' +
                    'http://' + host + ':' + this.port + '</a>。</li></ol>';
            } else {
                title = "Gopeed 还没准备好";
                body = '<p style="margin:0 0 14px">套件可能仍在启动，等几秒后关掉窗口重新打开；' +
                    '若一直如此，请到「套件中心」确认 Gopeed 状态是"运行中"。</p>' +
                    '<p style="margin:0"><a href="http://' + host + ':' + this.port +
                    '" target="_blank" rel="noopener">在新窗口打开 Gopeed</a></p>';
            }

            return '<div style="padding:28px;font:14px/1.8 -apple-system,\'Segoe UI\',Arial,sans-serif;color:#333">' +
                '<h3 style="margin:0 0 10px">' + title + '</h3>' + body + '</div>';
        },

        // Over https a self-signed certificate fails silently inside an iframe,
        // so probe the API first: success -> embed, failure -> explain.
        probe: function (cmp) {
            var self = this;
            var settled = false;

            function finish(html) {
                if (settled) return;
                settled = true;
                cmp.update(html);
            }

            var timer = setTimeout(function () {
                finish(self.noticeHtml("startup"));
            }, 5000);

            try {
                fetch(self.url() + "api/v1/info", { mode: "no-cors", cache: "no-store" })
                    .then(function () {
                        clearTimeout(timer);
                        finish(self.iframeHtml());
                    })
                    .catch(function () {
                        clearTimeout(timer);
                        finish(self.noticeHtml("tls"));
                    });
            } catch (e) {
                clearTimeout(timer);
                finish(self.noticeHtml("tls"));
            }
        },

        component: function () {
            var self = this;
            if (window.location.protocol !== "https:") {
                return {
                    xtype: "component",
                    autoEl: {
                        tag: "iframe",
                        src: self.url(),
                        frameborder: "0",
                        style: IFRAME_STYLE
                    }
                };
            }
            return {
                xtype: "component",
                autoEl: { tag: "div", style: "height:100%", html: self.noticeHtml("probing") },
                listeners: {
                    afterrender: function (cmp) {
                        self.probe(cmp);
                    }
                }
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
            width: 1200,
            height: 800,
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

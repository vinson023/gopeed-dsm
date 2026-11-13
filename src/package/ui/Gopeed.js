/*
 * DSM 7 desktop integration for Gopeed.
 *
 * The window hosts an iframe pointing at a SAME-ORIGIN path:
 *     /webman/3rdparty/gopeed/app/index.html
 *
 * Same origin is mandatory: DSM sends `Content-Security-Policy: frame-src
 * 'self' ...`, and 'self' includes the port. An iframe to http://nas:38099
 * (a different port) is refused by the browser and the window stays empty,
 * no matter whether DSM itself was opened over http or https.
 *
 * The front-end bundle is shipped inside this package (ui/app/) and served by
 * DSM's own nginx, so it loads under the DSM origin. Gopeed's REST engine still
 * listens on 38099; ui/app/index.html rewrites /api/* to that port, which the
 * CSP permits because it allows `connect-src http: https:`.
 *
 * Structure follows the working third-party package on this NAS (arc-control):
 * a plain AppInstance + AppWindow with a single box/iframe item. Nothing else.
 */
Ext.define("SYNO.SDS.Gopeed.Application", {
    extend: "SYNO.SDS.AppInstance",
    appWindowName: "SYNO.SDS.Gopeed.MainWindow",
    constructor: function () {
        this.callParent(arguments);
    }
});

Ext.define("SYNO.SDS.Gopeed.MainWindow", {
    extend: "SYNO.SDS.AppWindow",
    constructor: function (cfg) {
        var config = Ext.apply({
            resizable: true,
            maximizable: true,
            minimizable: true,
            width: 1200,
            height: 800,
            minWidth: 800,
            minHeight: 600,
            layout: "fit",
            border: false,
            cls: "syno-app-gopeed-wrapper",
            items: [{
                xtype: "box",
                itemId: "appframe",
                autoEl: {
                    tag: "iframe",
                    src: "/webman/3rdparty/gopeed/app/index.html?v=" + Date.now(),
                    frameborder: "0",
                    style: "width:100%; height:100%; border:none;"
                }
            }]
        }, cfg);
        this.callParent([config]);
    }
});

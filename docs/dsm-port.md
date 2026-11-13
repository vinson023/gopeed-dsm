# 群晖 DSM 7 移植说明

同源的飞牛（fnOS）版本见另一个仓库。两者共用同一个上游二进制，但**生命周期与权限模型
完全不同**，所以打包方式也不是简单改几个路径。

## 一、和 fnOS 版最大的差别：不能要 root

DSM 7 的规定（官方开发者文档）：

> unsigned packages requiring root privilege are not installable in the ordinary way
> unless you have a Synology-issued development token

也就是说，未签名套件只要 `conf/privilege` 声明 `run-as: root`，套件中心会直接拒绝安装。
所以：

| | fnOS 版 | DSM 版 |
|---|---|---|
| 运行身份 | `run-as=root` + `setpriv` 降权到 NAS 用户 | `run-as=package`（套件用户 `gopeed`） |
| 写用户目录 | 以该 NAS 用户身份写 | 需要「套件中心 → 权限」授权共享文件夹 |
| 文件属主 | 选的 NAS 用户 | `gopeed` |
| HTTPS | 内置隧道，复用 fnOS 证书按 SNI 选择 | 用 DSM 原生反向代理 |
| 依赖 | 系统 `python3` | 只用 `bash` + `curl`（DSM 7 没有 python3） |

DSM 版因此更简单（没有降权、没有 TLS 隧道），代价是下载文件的属主是套件用户。

## 二、套件结构

```
gopeed-<version>-<arch>.spk   (tar.gz)
├── INFO                      元数据
├── package.tgz               解压到 /var/packages/gopeed/target
│   └── bin/gopeed            上游二进制（755）
├── scripts/
│   ├── preinst               端口占用检查
│   ├── postinst              建目录、写配置、修属主
│   ├── start-stop-status     start / stop / status
│   ├── preuninst             可选清空配置与数据
│   ├── postuninst            清临时目录
│   ├── preupgrade / postupgrade  升级时保数据、修权限
├── conf/
│   ├── privilege             run-as=package
│   └── resource              data-share: gopeed
├── WIZARD_UIFILES/
│   ├── install_uifile        账号 / 密码 / 下载目录 / BT / ED2K 端口
│   └── uninstall_uifile      保留 / 清空
├── PACKAGE_ICON.PNG          64x64（DSM 7 要求，DSM 6 才是 72x72）
├── PACKAGE_ICON_256.PNG      256x256
└── LICENSE                   GPL-3.0（< 1 MB）
```

`INFO` 的关键字段：

```
package="gopeed"
version="1.9.3-13"          # x.y.z 或 x.y.z-b，不接受四段
arch="x86_64"               # 另一包为 armv8
os_min_ver="7.0-40000"      # DSM 7 最低要求
thirdparty="yes"
startable="yes"
adminport="38099"           # 桌面图标 = http://<host>:38099
adminprotocol="http"
checkport="38099"
checksum="<package.tgz 的 MD5>"
```

`checksum` 写错会直接报 "corrupt package"，所以 `build.py` 在生成 `package.tgz`
之后计算再写回 `INFO`。

## 三、包格式（DSM 7 最容易踩的坑）

`.spk` **本身必须是未压缩的 tar**：套件中心与 `synopkg` 都只认裸 tar，
外面再套一层 gzip 会直接报「套件文件格式不正确」（`synopkg` 返回
`code 263: failed to sort spks, the spk might not exist or invalid format`）。
只有里面的 `package.tgz` 是压缩的。

tar 的**内部格式**也要用 GNU（或 USTAR），不能用 PAX：Python 的 `tarfile`
默认写 PAX，会插入扩展头（`pax_headers`），DSM 的解析器同样不认。

```python
# 对
with tarfile.open(spk, "w", format=tarfile.GNU_FORMAT) as tar:   # 未压缩 + GNU
    ...
with tarfile.open(tgz, "w:gz", format=tarfile.GNU_FORMAT) as tar:
    ...

# 错（两种都会让安装失败）
# tarfile.open(spk, "w:gz")              -> gzip 外壳
# tarfile.open(spk, "w")                 -> PAX 扩展头
```

自查：

```python
with tarfile.open(spk) as t:
    assert any(m.type == b'x' for m in t.getmembers()) is False   # 没有 PAX 头
print(open(spk,'rb').read(262)[257:262])   # b'ustar'，而不是 gzip 的 1f8b
```

## 三之二、DSM 桌面集成（主菜单图标 + 窗口内嵌）

只写 `adminport` 时，套件只会出现在「套件中心」，那里的"打开"按钮会**新开一个
页签**指向 `http://host:38099`，主菜单（桌面）里没有图标。

让应用出现在主菜单、并在 DSM 窗口内打开，需要：

1. `INFO` 里声明 `dsmuidir="ui"` 与 `dsmappname="SYNO.SDS.Gopeed.Application"`；
2. `package.tgz` 里带 `ui/config`（类声明）、`ui/Gopeed.js`、`ui/images/icon_{16,24,32,48}.png`；
3. DSM 自动把 `ui/` 软链到 `/usr/syno/synoman/webman/3rdparty/gopeed/`。

`ui/config` 只需要两个类（照这台 NAS 上能正常工作的第三方套件 `arc-control` 的写法）：

```json
{
  "Gopeed.js": {
    "SYNO.SDS.Gopeed.Application": {
      "type": "app",
      "title": "Gopeed",
      "appWindow": "SYNO.SDS.Gopeed.MainWindow",
      "icon": "images/icon_{0}.png",
      "allowMultiInstance": false,
      "depend": ["SYNO.SDS.Gopeed.MainWindow"]
    },
    "SYNO.SDS.Gopeed.MainWindow": {
      "type": "lib",
      "title": "Gopeed",
      "icon": "images/icon_{0}.png"
    }
  }
}
```

`ui/Gopeed.js` 里 `AppInstance` + `AppWindow`，窗口内容是一个 `xtype: "box"` 的
iframe（必须用 `autoEl`，见下）：

```javascript
Ext.define("SYNO.SDS.Gopeed.Application", {
    extend: "SYNO.SDS.AppInstance",
    appWindowName: "SYNO.SDS.Gopeed.MainWindow",
    constructor: function () { this.callParent(arguments); }
});

Ext.define("SYNO.SDS.Gopeed.MainWindow", {
    extend: "SYNO.SDS.AppWindow",
    constructor: function (cfg) {
        this.callParent([Ext.apply({
            layout: "fit", border: false,
            width: 1200, height: 800, minWidth: 800, minHeight: 600,
            items: [{
                xtype: "box",
                autoEl: { tag: "iframe",
                          src: "/webman/3rdparty/gopeed/web/index.html?v=" + Date.now(),
                          style: "width:100%;height:100%;border:none;" }
            }]
        }, cfg)]);
    }
});
```

> **踩过的坑：一直显示"应用正在打开中"**
> 最早的版本在 `MainWindow` 的 constructor 里读了 `cfg.appInstance`，并额外声明了
> `SYNO.SDS.Gopeed.Utils`（在 config 里写成空数组 `[]`）。这两处任意一个出错都会让
> DSM 的窗口停在 loading 遮罩上 —— 桌面显示"一直在打开中"，永远不出画面。
> 现在的版本和 `arc-control` 完全同构：没有多余的类、不读未定义的属性、
> 只把 `cfg` 透传给 `AppWindow`。

验证：

```bash
ls -ld /usr/syno/synoman/webman/3rdparty/gopeed          # 软链
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/webman/3rdparty/gopeed/Gopeed.js
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/webman/3rdparty/gopeed/web/index.html
```

## 三之三、CSP：为什么 iframe 必须同源（这是最关键的一条）

DSM 每个页面都带这个响应头（`curl -sI http://nas:5000/` 可直接看到）：

```
Content-Security-Policy:
  base-uri 'self';
  connect-src data: ws: wss: http: https:;
  default-src 'self' 'unsafe-eval' data: blob: https://*.synology.com ...;
  font-src 'self' data: https://*.googleapis.com https://*.gstatic.com;
  frame-ancestors 'self';
  frame-src 'self' data: blob: https://*.synology.com https://*.synology.cn;
  script-src 'self' 'unsafe-eval' data: blob: https://maps.google.com ...;
  style-src 'self' 'unsafe-inline' https://*.googleapis.com;
X-Frame-Options: SAMEORIGIN
```

两个要点：

1. `frame-src 'self'` 里的 **`self` = scheme + host + port**。`http://nas:38099`
   和 DSM 的 `http://nas:5000` 端口不同就是不同源，浏览器**直接拒绝**加载这个
   iframe，控制台报 `Refused to frame ... frame-src 'self'`。窗口因此全白，
   **与协议、证书、iframe 高度都无关**（我最初误判成"高度 0"，走了两轮弯路）。
2. `connect-src` 却是 **`http: https:` 全放开** —— 也就是说同源页面里的
   JS 可以自由 fetch 到 38099，只是需要 CORS 头。

所以唯一可行的结构是「全部走 DSM 自己的 origin」：

```
iframe（同源）  /webman/3rdparty/gopeed/web/index.html
      │  静态资源由 DSM nginx 直接从 target/ui/web/ 提供
      ▼
   Gopeed 前端（Flutter）
      │  XHR/fetch → /webman/3rdparty/gopeed/api.cgi/api/...
      ▼
   api.cgi（DSM 在 webman/3rdparty 下执行 CGI，先降权到 gopeed）
      ▼
   gopeed 引擎（127.0.0.1:38099）
```

同源意味着：**http 和 https 自动都可用**，不需要证书、不需要 TLS 隧道、
不需要 CORS —— 浏览器根本没看到跨域请求。

试过并排除的其它路子：

| 方案 | 结果 |
|---|---|
| iframe 指向 `http://nas:38099` / `https://nas:38099` | ❌ 跨端口，`frame-src 'self'` 拦截 |
| 让 nginx 反代 `/gopeed` 到 38099 | ❌ 套件以 package 用户运行，写不了 nginx 配置也 reload 不了 |
| 改 CSP 放行该端口 | ❌ 头由 DSM 主程序输出，`/etc/nginx`、`webman` 目录里都搜不到 |
| `data:` / `blob:` iframe | ❌ CSP 允许，但子页面继承父 CSP，`script-src 'self'` 不允许加载 38099 的 JS |
| 窗口里塞 `<iframe>` 字符串（`html:`） | ❌ Ext 容器不给子节点分配高度，iframe 算出 0 |

## 三之四、同源方案怎么落地（参考 arc-control）

### 别人是怎么做到的

这台 NAS 上装过一个第三方套件 `arc-control`，它在 http 和 https 下都能正常打开。
它的做法是：**前端静态文件 + CGI 都在自己的 `ui/` 目录里**，由 DSM 自己提供和执行。

```js
// /var/packages/arc-control/target/app/dsm-wrapper.js
items: [{ xtype: "box", autoEl: {
    tag: "iframe",
    src: "/webman/3rdparty/arc-control/ui/index.html?v=" + Date.now()
}}]
```

它的前端调接口也是同源路径：

```
/webman/3rdparty/arc-control/scripts/api.cgi
```

关键事实：**DSM 的 nginx 会在 `/webman/3rdparty/<pkg>/` 下执行 `.cgi` 文件**，
而且 `PATH_INFO`、`QUERY_STRING`、`REQUEST_METHOD`、`CONTENT_LENGTH` 都正常传入
（已实测）。所以套件不需要第二个端口、不需要反代、不需要证书。

实测 `PATH_INFO`：

```
GET /webman/3rdparty/gopeed/api.cgi/api/v1/tasks?status=done
  -> PATH_INFO    = /api/v1/tasks
  -> QUERY_STRING = status=done
```

### 四步落地

**1）前端资源随包分发**

上游的 web 构建是 Go 内嵌的，磁盘上没有独立文件，构建时从运行中的引擎抓取：

```bash
B=http://127.0.0.1:38099
for p in index.html flutter_bootstrap.js main.dart.js manifest.json favicon.png \
         canvaskit/canvaskit.js canvaskit/canvaskit.wasm \
         assets/AssetManifest.bin assets/FontManifest.json \
         assets/fonts/MaterialIcons-Regular.otf \
         icons/Icon-192.png icons/Icon-512.png; do
    mkdir -p "ui/web/$(dirname $p)"
    curl -s -o "ui/web/$p" "$B/$p"
done
# 注意 /index.html 会 301 到 /，要 -L 或者直接抓 /
```

好消息：**Gopeed 前端本来就用相对路径**（`index.html` 里没有 `<base href="/">`，
`flutter_bootstrap.js` 用 `document.baseURI` 解析），所以放进子目录不会 404。

**2）`index.html` 把 API 指到同源 CGI**

Gopeed 的请求路径混着写：`api/v1/resolve`（相对）和 `/api/v1/tasks`（绝对根）都有。
统一改写成 CGI 路径（`PATH_INFO` 直接承载真实路径，不用 URL 编码）：

```javascript
var API_CGI = "/webman/3rdparty/gopeed/api.cgi";

function rewrite(u) {
  if (typeof u !== "string" || u.indexOf("api/") < 0) return u;
  if (u.indexOf("api.cgi") >= 0) return u;              // 已经改过，防止递归
  var m = u.match(/^(?:https?:\/\/[^\/]*)?\/?(api\/.*)$/);
  return m ? API_CGI + "/" + m[1] : u;                  // -> api.cgi/api/v1/tasks
}
// patch XMLHttpRequest.prototype.open / window.fetch / EventSource
```

**3）`ui/api.cgi`：同源反向代理**

```python
UPSTREAM = "http://127.0.0.1:38099"
path  = os.environ["PATH_INFO"]        # /api/v1/tasks
query = os.environ["QUERY_STRING"]     # status=done
# 转发 method / body / Authorization / X-Api-Token / Content-Type
# 输出 Status: + 头 + body（去掉 transfer-encoding / connection / content-length）
```

**DSM 以 root 执行套件 CGI**，所以脚本开头会 `setgid/setuid` 到 `gopeed`
（实测 `euid 0 -> 154548 gopeed`）。

> 只允许 `/api/` 前缀，避免变成任意 URL 代理。

**4）必须同时带 `canvaskit/chromium/`**

Flutter 在 Chrome/Edge（blink 引擎）下不会加载 `canvaskit/canvaskit.js`，
而是动态 import `canvaskit/chromium/canvaskit.js` + `chromium/canvaskit.wasm`。
只抓 `canvaskit/` 根目录那两个文件，控制台会报：

```
Failed to fetch dynamically imported module: .../canvaskit/chromium/canvaskit.js
TypeError: Failed to execute 'compile' on 'WebAssembly': HTTP status code is not ok
```

窗口就是空白。所以抓资源时这两个目录都要拿：

| 浏览器 | 用到的文件 |
|---|---|
| Chrome / Edge | `canvaskit/chromium/canvaskit.js` + `.wasm`（5.7 MB） |
| Firefox / Safari | `canvaskit/canvaskit.js` + `.wasm`（7.2 MB） |

**5）关闭 Flutter 的 service worker，并换一个目录名**

`flutter_bootstrap.js` 末尾 `_flutter.loader.load({ serviceWorkerSettings: ... })`
改成 `_flutter.loader.load({})`。

但**仅仅禁用不够**：早期版本已经注册过的 SW 会一直留在浏览器里，
按旧的资源清单返回缓存（其中没有 `chromium/`），更新的文件也不会生效。
最稳妥的做法是**把 bundle 目录换个名字**（本项目从 `app/` 改成 `web/`），
旧 SW 的 scope 是 `/webman/3rdparty/gopeed/app/`，新路径根本不在它的 scope 内。
index.html 里再补一句主动注销，双保险：

```javascript
navigator.serviceWorker.getRegistrations().then(function (rs) {
  rs.forEach(function (r) { r.unregister(); });
});
caches.keys().then(function (ks) { ks.forEach(function (k) { caches.delete(k); }); });
```

`flutter_bootstrap.js` 末尾 `_flutter.loader.load({ serviceWorkerSettings: ... })`
改成 `_flutter.loader.load({})`。否则子目录下注册的 SW 会把旧版本缓存住。

**6）字体必须一起抓，否则整个界面没有文字**

Flutter 的 `FontManifest.json` 里列了三处字体，路径容易抓错（注意 `assets/assets/` 是**两个**
`assets`，Flutter 会自己再加一层前缀）：

| 用途 | 服务器上的真实路径 | 抓错会怎样 |
|---|---|---|
| 正文字体 | `assets/gstatic/roboto/v32/KFOmCnqEu92Fr1Me4GZLCzYlKw.woff2` | 所有文字不显示 |
| Gopeed 图标字体 | `assets/assets/fonts/Gopeed.ttf` | 部分图标缺失 |
| Cupertino 字体 | `assets/packages/cupertino_icons/assets/CupertinoIcons.ttf` | iOS 风格图标缺失 |
| Material 图标 | `assets/fonts/MaterialIcons-Regular.otf` | 图标全变方块 |

抓漏时的控制台提示（这些不是"可以忽略的警告"，而是**文本渲染会直接失败**）：

```
Font family Roboto not found (404) at assets/gstatic/roboto/v32/KFOmCnqEu92Fr1Me4GZLCzYlKw.woff2
Font family Gopeed not found (404) at assets/assets/fonts/Gopeed.ttf
```

实测现象：界面布局、图标、按钮全都正常，**唯独没有任何文字** —— 因为 CanvasKit
没有可用字形。

**6.1）中文必须单独带 Noto Sans SC 分片**

Roboto 只有拉丁字形，汉字要靠 CanvasKit 的 `FontFallbackManager` 现场下载。
`main.dart.js` 里硬编码的 fallback base 是 **`assets/gstatic/`**，Noto Sans SC
在 Google Fonts 上被切成 **101 个 woff2 分片**（`notosanssc/v37/<hash>.<n>.woff2`）。

如果 NAS 或浏览器访问不了 `fonts.gstatic.com`，中文全部渲染成 **方块**（英文正常），
这就是"中文变方块"的原因。做法是把 101 个分片全部下载进
`ui/web/assets/gstatic/notosanssc/v37/`（约 2.4 MB），fallback 会自动命中本地文件：

```python
import concurrent.futures, os, re, urllib.request
src = open("main.dart.js", encoding="utf-8", errors="replace").read()
paths = sorted(set(re.findall(r"notosanssc/v37/[A-Za-z0-9_.-]+\.woff2", src)))
os.makedirs("assets/gstatic/notosanssc/v37", exist_ok=True)
def fetch(p):
    dst = os.path.join("assets/gstatic", p)
    with urllib.request.urlopen("https://fonts.gstatic.com/s/" + p, timeout=30) as r:
        open(dst, "wb").write(r.read())
with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
    list(ex.map(fetch, paths))
```

同样的思路适用于繁体（`notosanstc`，105 片）、日文（`notosansjp`，124 片）、
韩文（`notosanskr`，124 片）—— 只做简体的话带 `notosanssc` 就够。

> 注意：这些分片是**浏览器**下载的，不是 NAS 下载的。NAS 能访问 gstatic
> 不代表用户的浏览器能访问，所以必须本地化。

**7）安装向导的 desc 必须短**

`WIZARD_UIFILES/install_uifile` 里 `subitems[].desc` 渲染在输入框左侧、**单行**，
超长就溢出（截图里出现过"测套件会启动失败。访问端口固定为…"被裁掉开头、
后面的端口输入框标签直接消失）。控制在 10 个汉字以内，例如
`"默认 51413"`、`"留空用共享目录"`；长的解释放到 README / 发布说明里。

### 仍然保留的隧道

`bin/gopeed-tunnel.py` 现在只服务**直连 38099** 的场景（有人直接收藏
`http://nas:38099`），它继续提供 http/https 同端口 + CORS。
桌面图标走的是 CGI，不依赖它。

## 三之五、HTTP 与 HTTPS 同端口

```
浏览器 ──► :38099 (gopeed-tunnel.py) ──► 127.0.0.1:38100 (gopeed)
                 │ 首字节 0x16 ? TLS : 明文
```

- 有证书 → http 和 https 都可用；没有证书 → 只跑 http（CORS 照常注入）
- 隧道双栈监听 `0.0.0.0` + `[::]`

**证书是硬限制**：DSM 的默认证书在 `/usr/syno/etc/certificate/system/default/`，
`privkey.pem` 是 `600 root:root`，而 DSM 7 的 `postinst` 以 package 用户运行
（实测 `sudo -l -U gopeed` → not allowed），套件自己复制不了，需要管理员手动一次：

```bash
sudo cp /usr/syno/etc/certificate/system/default/fullchain.pem /volume1/@appdata/gopeed/tls/cert.pem
sudo cp /usr/syno/etc/certificate/system/default/privkey.pem   /volume1/@appdata/gopeed/tls/privkey.pem
sudo chown -R gopeed /volume1/@appdata/gopeed/tls
sudo synopkg restart gopeed
```

**群晖自签证书在 iframe 里无效**：浏览器对 iframe 内的证书错误不弹"继续访问"，
直接阻断。所以 https 桌面必须配受信任证书（Let's Encrypt 等）。
只在内网用 `http://nas:5000` 打开 DSM 则没有这个限制。

## 三之六、两个和「配置」有关的坑（都踩过）

### 1. `postinst` 不能无条件重写 conf

最初 `postinst` 每次都 `cat > gopeed.conf`，用向导变量生成配置。命令行 / 套件中心
**升级**时向导变量是空的，于是账号、端口、下载目录全被重置成默认值 ——
表现为"升级后设置没了"。

正确做法：只在 conf 不存在（首次安装）时生成，升级时原样保留：

```bash
if [ -f "$CONF" ]; then
    . "$CONF"                       # 读回用户设置，后面 chown 之类继续用
else
    cat > "$CONF" <<EOF
...
EOF
fi
```

卸载时选「保留配置」则 conf 还在，重装也会沿用；选「全部清空」才会重新生成。

### 2. 下载目录不可写不能当成致命错误

DSM 共享文件夹的权限是 **ACL**，`ls -l` 显示 `drwxrwxrwx+` 不代表套件用户能写
（那个 `+` 就是 ACL）。用户如果在向导里填了 `/volume1/downloads` 却没在
「套件中心 → 套件 → 权限」里授权，`test -w` 就是失败。

早期版本直接 `return 1`，导致**整个套件起不来**（`status: start_failed`）。
正确做法是回退：

```bash
if [ -n "$DOWNLOAD_DIR" ] && [ ! -w "$DOWNLOAD_DIR" ]; then
    log "download dir not writable: $DOWNLOAD_DIR ...; using the package share instead"
    DOWNLOAD_DIR=""                 # 触发回退
fi
[ -n "$DOWNLOAD_DIR" ] || DOWNLOAD_DIR="${VOL}/@appshare/${PKG}/downloads"
```

授权可以事后补，用 DSM 自带的 ACL 工具（root）：

```bash
sudo /usr/syno/bin/synoacltool -add /volume1/downloads \
     "user:gopeed:allow:rwxpdDaARWc--:fd--"
sudo /usr/syno/bin/synoacltool -get /volume1/downloads     # 确认
```

> 注意 `postinst` 里不要用 `su -c "test -w"` 预检：它和真实 ACL 结果不一致
> （实测把可写目录判成只读）。运行时检查放在 `start-stop-status`，那里本身
> 就以套件用户身份运行。

## 四、启动参数

```
bin/gopeed -A "[::]" -P 38099 -d $SYNOPKG_PKGVAR/storage -c $SYNOPKG_PKGVAR/config.json \
           -u <user> -p <pass>
```

- `-A` 只接受纯地址，引擎内部用 `fmt.Sprintf("%s:%d", address, port)` 拼接。
  传 `::` 会得到 `:::38099` 并 panic（`too many colons`），**必须传 `[::]`**。
- 内核 `bindv6only=0` 时 `[::]` 是双栈；否则退回 `0.0.0.0`。
- 密码为空时不传 `-u/-p`，引擎不启用鉴权。

## 五、配置的三个坑

### 1. `GOPEED_DOWNLOAD_CONFIG` 在这个构建里不生效

虽然 `cmd/web/flags.go` 支持 `GOPEED_DOWNLOAD_CONFIG`（映射到 `args.DownloadConfig`），
但实测 1.9.3 的 web 构建不会把它作为初始值。有效的是 `-c` 文件里的 `downloadConfig`：

```json
{ "downloadConfig": { "downloadDir": "...", "protocolConfig": { "bt": {...} } } }
```

### 2. 这个 seed 文件每次启动都会覆盖存储里的配置

`downloadConfig` 名义上只在首次初始化时使用，但实测如果每次启动都保留该文件，
存储里的配置会被写成零值 —— 最明显的是 `maxRunning: 0`，任务全部卡在 `wait` 不下载。

所以脚本只在 `storage` 为空时写 `config.json`，之后就删掉它：

```bash
if [ ! -d "$VAR/storage" ] || [ -z "$(ls -A "$VAR/storage")" ]; then
    cat > "$VAR/config.json" <<JSON
{ "downloadConfig": ${dlcfg} }
JSON
else
    rm -f "$VAR/config.json"
fi
```

### 3. `PUT /api/v1/config` 要的是 data 对象，不是整个响应

`GET /api/v1/config` 返回 `{"code":0,"msg":"","data":{...}}`。如果把整个响应体 PUT
回去，配置会被重置成零值（`downloadDir` 变空、`maxRunning` 变 0）。必须只发 `data`：

```bash
data="$(printf '%s' "$raw" | sed -n 's/.*"data":\({.*}\)}[^}]*$/\1/p')"
body="$(printf '%s' "$data" | sed 's#"downloadDir":"[^"]*"#"downloadDir":"'"$DL"'"#')"
```

这也是为什么 `sync_download_dir()` 在 PUT 前要校验 `data` 里确实含 `"downloadDir"`。

## 六、实测记录

**真机**：DSM 7.4.1-90080（x86_64），通过 `synopkg install` 安装并运行。

| # | 项目 | 结果 |
|---|---|---|
| 1 | spk 格式（未压缩 tar + GNU） | ✅ 安装成功；gzip 外壳与 PAX 头都会报「格式不正确」 |
| 2 | `preinst` / `postinst` | ✅ 均 code 0 |
| 3 | 启动 / 状态 / 停止 / 重启 | ✅ `status: running`，重启后自动恢复 |
| 4 | `data-share` | ✅ 自动创建 `/volume1/@appshare/gopeed`（属主 `gopeed`） |
| 5 | 默认值（命令行安装无向导） | ✅ `admin` / `gopeed` / `@appshare/gopeed/downloads` / 51413 / 4662 / 4672 |
| 6 | 首启播种 | ✅ `maxRunning=5`、端口与目录均写入引擎 |
| 7 | 实际下载 | ✅ 39.7 MB 与 35 KB 两个任务落盘，属主 `gopeed` |
| 8 | 重启后配置保持 | ✅ `downloadDir`/`maxRunning`/BT/ED2K 全部不变，seed 文件已删除 |
| 9 | IPv6 双栈 | ✅ `tcp6 :::38099` 监听，IPv4/IPv6 均可访问 |
| 10 | UI / API | ✅ `/` 200、登录 200 |
| 11 | 进程身份 | ✅ 以套件用户 `gopeed` 运行 |
| 12 | 同源 CGI 代理 | ✅ `/webman/3rdparty/gopeed/api.cgi/api/v1/info` 401（未授权）、登录 200 + token、带 token 取 config 200、创建任务 200 且文件落盘属主 `gopeed` |
| 13 | **https 下的 CGI** | ✅ `https://127.0.0.1:5001/webman/3rdparty/gopeed/...`：index 200、canvaskit.wasm 200、api 401（正常未授权） |
| 14 | CGI 运行身份 | ✅ DSM 以 root 执行，脚本 `setgid/setuid` 到 `gopeed`（实测 euid 0 → 154548） |

### 已知问题

- **首次创建任务时偶发崩溃**：一次 `panic: invalid memory address` 出现在
  `internal/protocol/http.(*Fetcher).Resolve`（上游 1.9.3），进程退出后
  `synopkg status` 仍显示 running（状态脚本读 pid）。重启后同样的任务正常完成，
  之后多次下载未再复现。属于上游缺陷，暂未找到稳定触发条件。
- 命令行安装不弹向导，所以向导字段渲染、桌面图标、共享文件夹授权、升级与卸载
  清理仍未在真机验证（见下）。

### 仍待验证

- 套件中心 GUI 安装时的向导渲染（`password`、`singleselect`）
- DSM 主菜单图标是否指向 `http://host:38099`
- 「套件中心 → 权限」授权后写入自定义共享文件夹
- 覆盖升级（同名 package、更高 version）
- 卸载时「全部清空」是否删净 `@appconf` / `@appdata`
- ARM 机型

## 七、构建校验

```bash
python build.py

# 结构
tar tzf build/gopeed-1.9.3-13-x86_64.spk

# checksum 是否与 INFO 一致
python - <<'PY'
import hashlib, tarfile
with tarfile.open("build/gopeed-1.9.3-13-x86_64.spk") as t:
    info = t.extractfile("INFO").read().decode()
    tgz = t.extractfile("package.tgz").read()
print("match:", hashlib.md5(tgz).hexdigest() in info)
PY

# 脚本语法
for f in src/scripts/*; do bash -n "$f" || echo "FAIL $f"; done
```

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

## 三之二、DSM 桌面集成（主菜单图标 + 窗口内打开）

只写 `adminport` 时，套件只会出现在「套件中心」，套件中心的"打开"按钮会
**新开一个页签**指向 `http://host:38099`，主菜单（桌面）里没有图标。

要让应用出现在主菜单并在 DSM 窗口内打开，需要三样东西：

1. `INFO` 里声明 `dsmuidir="ui"` 和 `dsmappname="SYNO.SDS.Gopeed.Application"`；
2. `package.tgz` 里带 `ui/config`（Ext 组件声明）和 `ui/Gopeed.js`；
3. DSM 会把 `ui/` 软链到 `/usr/syno/synoman/webman/3rdparty/gopeed/`，
   主菜单图标从 `ui/images/icon_{16,24,32,48}.png` 取图。

**关键**：DSM 7.2 起，`config` 里的 `"type": "legacy" | "url"` 已不再决定打开方式
（`legacy` 曾表示 iframe 内嵌）。现在必须按 Ext 应用来声明，由
`getMainHtml()` 返回 iframe：

```javascript
Ext.apply(SYNO.SDS.Gopeed.Utils, function () {
    return {
        getMainHtml: function () {
            return '<iframe src="http://<nas>:38099/" style="width:100%;height:100%;border:0"></iframe>';
        }
    };
}());
```

`config` 里对应的三个类：

```json
{ "Gopeed.js": {
    "SYNO.SDS.Gopeed.Application": { "type": "app", "appWindow": "SYNO.SDS.Gopeed.MainWindow", ... },
    "SYNO.SDS.Gopeed.MainWindow":  { "type": "lib", "depend": ["SYNO.SDS.Gopeed.Utils"] },
    "SYNO.SDS.Gopeed.Utils": [] } }
```

验证：

```bash
ls -la /usr/syno/synoman/webman/3rdparty/ | grep gopeed   # 软链是否存在
curl -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/webman/3rdparty/gopeed/Gopeed.js
```

两者都正常后，刷新 DSM 页面（或重新登录）主菜单就会出现图标。

**混合内容限制**：Gopeed 只有 HTTP。如果 DSM 本身用 https 打开，iframe 里的
`https://host:38099` 连不上（没有 TLS），`http://` 又会被浏览器拦截。这种情况下
`getMainHtml()` 会返回一段说明而不是空白的 iframe，并提示改用
`http://host:5000` 访问 DSM，或给 38099 配反向代理。

## 三之三、HTTP 与 HTTPS 同端口

DSM 桌面 iframe 继承 DSM 页面的协议：页面是 https，iframe 就必须是 https；页面是 http，
iframe 用 http。Gopeed 只有 HTTP，所以套件里带了一个小隧道：

```
浏览器 ──► :38099 (gopeed-tunnel.py) ──► 127.0.0.1:38100 (gopeed)
                 │ 首字节 0x16 ? TLS : 明文
```

- 有证书 → 同时支持 http 和 https；没有证书 → 降级为纯 HTTP（引擎直接绑 38099）
- 隧道进程和引擎都以套件用户运行，隧道双栈监听 `0.0.0.0` + `[::]`

**证书是硬限制**：DSM 的默认证书在 `/usr/syno/etc/certificate/system/default/`，
`privkey.pem` 权限是 `600 root:root`。DSM 7 的 `postinst` 以 **package 用户** 运行
（实测 `sudo -l -U gopeed` → not allowed），所以套件自己复制不了证书，必须在安装后
由管理员手动复制一次：

```bash
sudo cp /usr/syno/etc/certificate/system/default/fullchain.pem /volume1/@appdata/gopeed/tls/cert.pem
sudo cp /usr/syno/etc/certificate/system/default/privkey.pem   /volume1/@appdata/gopeed/tls/privkey.pem
sudo chown -R gopeed /volume1/@appdata/gopeed/tls
sudo synopkg restart gopeed
```

另外一个前提：**自签证书在 iframe 里没有用**。浏览器对 iframe 内的证书错误不会弹
"继续访问"，直接阻断。所以 https 场景必须配受信任的证书（Let's Encrypt 等）。

## 三之四、为什么不能把 Gopeed 嵌进 DSM 窗口（CSP）

DSM 的所有页面都带这个响应头：

```
Content-Security-Policy: ... frame-src 'self' data: blob: https://*.synology.com ...
```

`frame-src 'self'` 的 `self` 包含 scheme + host + **port**，所以
`http://nas:38099` 与 DSM 的 `http://nas:5000` 不同源 —— 浏览器会**直接阻止**
这个 iframe，控制台报 "Refused to frame ... because it violates the following
Content Security Policy directive: frame-src 'self'"。表现就是窗口一片空白，
与 iframe 高度、协议、证书都无关。

尝试过的路和结论：

| 方案 | 结果 |
|---|---|
| iframe 指向 `http://nas:38099` | ❌ CSP 拦截 |
| iframe 指向 `https://nas:38099` | ❌ 同样跨端口，CSP 拦截 |
| 把 Gopeed 挂到 `/webman/3rdparty/gopeed/app/` 反代 | ❌ 前端与 REST 用绝对路径 `/api/...`，全部 404 |
| 改 CSP 放行该端口 | ❌ 头来自 DSM 主程序，nginx 配置和 webman 目录里都搜不到，改不了也不该改 |

最终方案：窗口里渲染一张启动卡片（`ui/Gopeed.js`），点按钮用
`window.open(url, "gopeed_app", "width=1280,height=860,menubar=no,...")`
打开一个没有地址栏/工具栏的独立窗口，观感接近应用窗口而不是浏览器标签页。

> 这也解释了为什么最早的版本（把 iframe 塞进 `html`）看起来像"高度 0"——
> 其实两个问题都有，但 CSP 才是致命的那个。

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

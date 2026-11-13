# Gopeed for Synology DSM 7

把 [Gopeed](https://github.com/GopeedLab/gopeed) 官方 headless Web 版打包成群晖 DSM 7
的套件（`.spk`）。纯 Native：不依赖 Docker / Node / Python，二进制静态链接，装完在
套件中心或 DSM 桌面点图标即用。

- 内置 Gopeed `1.9.3`（`cmd/web`，`-tags nosqlite,web`，内嵌 Flutter Web UI）
- 提供 `x86_64` 与 `armv8` 两个套件（含原生二进制，必须按机型选择）
- 安装向导可设：登录账号密码、默认下载目录、BT / ED2K 监听端口
- 下载文件属主是套件用户（`gopeed`），可在 File Station 中正常管理
- 卸载时可选择是否一并清空配置与任务数据

> **验证状态**：已在 DSM 7.4.1（x86_64）真机安装并运行 —— 安装、启动、下载、
> 重启后配置保持、IPv6 双栈均通过。ARM 包与套件中心的向导界面仍待验证，
> 详见 [docs/dsm-port.md](docs/dsm-port.md)。

## 安装

1. 到 [Releases](../../releases) 下载对应架构的套件：
   - Intel / AMD 机型 → `gopeed-<version>-x86_64.spk`
   - ARM 机型（armv8：rtd1296 / rtd1619 / armada37xx 等）→ `gopeed-<version>-armv8.spk`
2. DSM →「套件中心 → 手动安装」，选择 `.spk`
3. 按向导填写（都有默认值）：

| 字段 | 默认 | 说明 |
|---|---|---|
| 登录账号 | `admin` | Gopeed 页面登录名 |
| 登录密码 | `gopeed` | **建议改掉**；留空 = 关闭登录保护 |
| 默认下载目录 | 空 | 留空 = 共享文件夹 `gopeed/downloads` |
| BT 端口 | `51413` | 避开 6881-6889（部分运营商限速） |
| ED2K TCP / UDP | `4662` / `4672` | 固定才可能拿到 eMule HighID |

4. 装完后 **DSM 主菜单里会出现 Gopeed 图标**（刷新页面或重新登录一次），
   点它会在 DSM 窗口内直接打开 Gopeed，不会另开页签；也可以直接访问
   `http://<NAS地址>:38099`

> **端口固定为 38099**：DSM 在安装时把 `adminport` 写进套件信息并据此生成入口，
> 装完无法在界面上改。需要别的端口请自行构建（见下文）。

### 下载到自己的共享文件夹

套件默认以 `package` 用户运行，对共享文件夹没有写权限。要用自己的文件夹：

1. 「套件中心 → Gopeed → 权限」里勾选目标共享文件夹（读写）；
2. 在安装向导的「默认下载目录」填它的绝对路径，例如 `/volume1/downloads`。

否则服务启动会失败，日志（`/volume1/@appdata/gopeed/gopeed.log`）会写明目录不可写。

### HTTPS

桌面窗口里直接内嵌 Gopeed（同源 iframe），API 通过隧道带 CORS 头访问 38099。
如果用 https 打开 DSM，iframe 也是 https，隧道会在同一个端口上按协议分流。

https 要真正可用，需要两件事：

1. **DSM 的默认证书是受信任的证书**（例如 DDNS 域名上的 Let's Encrypt）。
   群晖自签证书在 iframe 里会被浏览器直接拒绝，且不会给"继续访问"的机会 ——
   这种情况下桌面窗口会显示一段说明，而不是空白。
2. **把证书复制到套件目录**（私钥是 `600 root`，套件用户读不到，必须手动一次）：

```bash
sudo cp /usr/syno/etc/certificate/system/default/fullchain.pem /volume1/@appdata/gopeed/tls/cert.pem
sudo cp /usr/syno/etc/certificate/system/default/privkey.pem   /volume1/@appdata/gopeed/tls/privkey.pem
sudo chown -R gopeed /volume1/@appdata/gopeed/tls
sudo synopkg restart gopeed
```

没有证书时隧道自动降级为纯 HTTP，不影响局域网使用。

## 构建

需要 Python 3；不需要 Synology 工具链（`.spk` 就是一个固定布局的 tar.gz）。

```bash
# 1. 取上游 headless 二进制（官方 release），放到
#    dist/<goarch>/gopeed-web-v<version>-linux-<goarch>/gopeed
#    amd64 -> dist/amd64/...   arm64 -> dist/arm64/...

# 2. 生成套件（默认端口 38099）
python build.py
python build.py --port 38123        # 换端口

# 产物
#   build/gopeed-<version>-x86_64.spk
#   build/gopeed-<version>-armv8.spk
```

`build.py` 用 `tarfile` 的 `GNU_FORMAT` 打包，`.spk` 外层不压缩（DSM 7 的硬要求）。

`build.py` 会把端口写入 `INFO`（`adminport` / `checkport`）、`scripts/preinst`、
`scripts/postinst`、`scripts/start-stop-status` 和向导文案，并计算 `package.tgz`
的 MD5 写进 `INFO` 的 `checksum`。

## 目录

```
.
├── build.py                    生成两个架构的 .spk
├── src/
│   ├── INFO                    套件元数据（arch / adminport / checksum 由 build.py 注入）
│   ├── PACKAGE_ICON*.PNG       套件中心图标（64 / 256）
│   ├── LICENSE                 Gopeed 的 GPL-3.0
│   ├── conf/privilege          run-as=package（DSM 7 不允许未签名包要 root）
│   ├── conf/resource           data-share: gopeed
│   ├── scripts/                preinst / postinst / start-stop-status / ...
│   ├── WIZARD_UIFILES/         install_uifile / uninstall_uifile
│   └── package/                打包进 package.tgz 的内容
│       ├── bin/gopeed          上游二进制
│       └── ui/                 DSM 桌面集成（config / Gopeed.js / images）
└── docs/dsm-port.md            移植说明与实测记录
```

## 关键设计

1. **必须 `run-as=package`**：DSM 7 会拒绝安装"要求 root 权限且未签名"的套件。
   因此不能像 Docker 那样以 root 启动再降权，整个服务以套件用户 `gopeed` 运行。
2. **下载目录靠 `config.json` 首启播种**：引擎在 `-c` 指定的文件里读 `downloadConfig`
   作为初始配置，但只在存储为空时生效；之后每次启动必须**删掉**该文件，否则它会把
   用户后来改的配置（包括 `maxRunning`）覆盖成零值。
3. **改过的下载目录靠 REST 补同步**：`@appdata` 在卸载后保留，重装时存储已存在，
   首启播种不会再生效。启动后用 `curl` 调 `GET/PUT /api/v1/config` 把向导里的目录写进去
   （只改 `downloadDir`，其余字段保持原样）。
4. **IPv6 双栈**：Gopeed 用 `"%s:%d"` 拼地址，所以传 `[::]`（带方括号）而不是 `::`，
   在 `bindv6only=0` 的内核上是双栈监听；否则退回 `0.0.0.0`。
5. **端口占用提前拦截**：`preinst` 里检查 38099，占用则中止安装（装完再改端口只能重装）。
6. **HTTP / HTTPS 同端口**：引擎绑 `127.0.0.1:38100`，`bin/gopeed-tunnel.py` 监听
   38099，按首字节分流（`0x16` = TLS）→ 同一个入口既能 http 也能 https，
   iframe 跟随 DSM 页面的协议即可，不需要反向代理。
   **但证书只能是 DSM 的默认证书**，而私钥是 `600 root`，套件以 package 用户运行读不到，
   所以需要手动复制一次（见「HTTPS」一节）；没有证书时自动降级为 HTTP。
7. **DSM 桌面集成必须同源**：`dsmuidir="ui"` 让 `package.tgz` 的 `ui/` 软链到
   `/usr/syno/synoman/webman/3rdparty/gopeed/`，`dsmappname` 指向 Ext 应用类。
   DSM 的响应头带 `Content-Security-Policy: ... frame-src 'self' ...`，
   而 `'self'` 包含**端口** —— 指向 `http://nas:38099` 的 iframe 会被浏览器直接拦掉，
   窗口全白。所以前端整包（`ui/app/`，12 MB）随套件分发，iframe 走同源路径
   `/webman/3rdparty/gopeed/app/index.html`；API 仍由 38099 提供，
   由隧道注入 CORS 头（DSM 的 `connect-src` 允许任意 http/https）。
8. **`.spk` 必须是未压缩的 tar，内部用 GNU 格式**：套件中心对 gzip 外壳和 PAX 扩展头
   都报「套件文件格式不正确」。只有里面的 `package.tgz` 才压缩。这条最容易踩，
   详见 [docs/dsm-port.md](docs/dsm-port.md) 第三节。

## 已知限制

- **端口不能安装时选**：`adminport` 写在 `INFO` 里，改端口 = 重新构建 + 卸载重装。
- **前端资源随包分发**：因为必须同源，`ui/app/` 里带了一份完整的 Flutter web 构建
  （约 12 MB），套件体积因此从 38 MB 涨到 42 MB。
- **https 需要受信任证书**：自签证书会触发浏览器警告，需要手动确认一次。
- **证书续期后要重新复制**：Let's Encrypt 续期会替换私钥，续期后重跑上面那三条
  `cp`/`chown` 命令（或重装套件）才会生效。
- **极少数情况下首次建任务会崩**：上游 1.9.3 在 `Fetcher.Resolve` 出现过一次
  nil pointer panic，重启后同样的任务正常完成，之后未再复现。
- **ARM 包未在真机运行**：仅通过架构字段与静态校验。
- **套件用户与共享文件夹**：默认只能写 `gopeed` 共享文件夹；其他目录需要在
  「套件中心 → 权限」里授权。
- **重装会重写配置**：安装向导的值会覆盖 `@appconf/gopeed/gopeed.conf`（保留任务数据，
  但账号密码/下载目录以本次向导为准）。

## 许可

打包脚本与套件配置以 **GPL-3.0** 发布；`src/LICENSE` 是随包分发所必需的 Gopeed 许可证。
Gopeed 版权归 [GopeedLab](https://github.com/GopeedLab/gopeed) 所有。

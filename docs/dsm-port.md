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

## 三、启动参数

```
bin/gopeed -A "[::]" -P 38099 -d $SYNOPKG_PKGVAR/storage -c $SYNOPKG_PKGVAR/config.json \
           -u <user> -p <pass>
```

- `-A` 只接受纯地址，引擎内部用 `fmt.Sprintf("%s:%d", address, port)` 拼接。
  传 `::` 会得到 `:::38099` 并 panic（`too many colons`），**必须传 `[::]`**。
- 内核 `bindv6only=0` 时 `[::]` 是双栈；否则退回 `0.0.0.0`。
- 密码为空时不传 `-u/-p`，引擎不启用鉴权。

## 四、配置的三个坑

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

## 五、实测记录

环境限制：手上没有群晖设备，因此在 Linux 上用 `SYNOPKG_*` 环境变量模拟 DSM 的调用方式，
以套件用户执行生命周期脚本（测试端口 38077，避免与同机其他服务冲突）。

| # | 项目 | 结果 |
|---|---|---|
| 1 | SPK 结构 / 权限位 | ✅ 脚本 755、`package.tgz` 内 `bin/gopeed` 755 |
| 2 | `INFO` 必填字段 / checksum | ✅ MD5 与 `package.tgz` 一致 |
| 3 | 向导 JSON 合法性 | ✅ 6 个安装字段 + 2 个卸载字段 |
| 4 | `postinst` 写配置 | ✅ `gopeed.conf` 600、属主 `gopeed` |
| 5 | 首启播种 | ✅ `downloadDir` / `bt=51413` / `ed2k=4662,4672` / `maxRunning=5` |
| 6 | 启动 / 状态 / 停止 | ✅ 以 `gopeed` 用户运行，`status` 0 = running |
| 7 | UI 与 API | ✅ `/` 200、登录 200 |
| 8 | 实际下载 | ✅ 文件落在指定目录，属主 `gopeed` |
| 9 | 重启后配置稳定性 | ✅ 不再被 seed 文件重置，seed 文件被删除 |
| 10 | 改目录后重启同步 | ✅ 引擎内 `downloadDir` 变为新值 |
| 11 | IPv6 双栈 | ✅ `[::]:38077` 上 IPv4/IPv6 均 200 |
| 12 | `-A ::` 的错误 | ❌ `too many colons`（已改为 `[::]`） |

### 待真机验证

- 套件中心安装/卸载向导的实际渲染（`password`、`singleselect` 字段）
- 桌面图标是否正常打开 `http://host:38099`
- `data-share` 是否创建 `gopeed` 共享文件夹
- 「套件中心 → 权限」授权后 `gopeed` 用户能否写入自定义目录
- 升级（同 `package` 名、更高 `version`）是否保留 `@appdata` / `@appconf`
- ARM 机型上的实际启动

## 六、构建校验

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

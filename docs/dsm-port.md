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

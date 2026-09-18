# ModelScope Manager — 项目开发手册（project.md）

> 读者：要在代码里干活的人 / AI。对外功能介绍看 `README.md`，本文件是内部地图。
> 维护纪律：**结构变更、架构决策变更必须当天同步本文件**，否则地图会腐烂成误导。

## 1. 发心
- **为谁**：Windows 上重度使用 ModelScope（魔搭）下载/上传模型与数据集的人。
- **解决什么**：官方只有 CLI/SDK，缺图形化仓库资源管理器；大模型文件动辄几十 GB，需要多线程断点续传；多账户、公共仓库、文件夹大小、媒体预览、备份、图床、WebDAV 挂载需要一套工具集中管理。
- **成功标准**：拖拽即管理远端仓库；下载断点续传 + SHA-256 校验；全部配置/数据收敛在一个 `data/` 目录。
- **非目标**：不做跨平台（Windows 专属）；不做 HF/CivitAI 等第三方模型站；不做多人协作；不做 Git 客户端（上传走 HTTP SDK，不引入 Git/LFS 环境）。

## 2. 快速开始
- **最终用户**：解压便携构建（含 `runtime` + `data`），双击 `start.bat`。
- **开发者**：`py -3.12 main.py`。`main.py` 检测到 `runtime/Lib/site-packages` 时优先注入 sys.path，避免误载用户环境里其他 Qt 绑定的同名 `qfluentwidgets`。
- **依赖**：PySide6、PySide6-Fluent-Widgets、pywebview、modelscope-hub、requests（仓库**无 requirements.txt**，便携构建自带；网页登录依赖系统 WebView2 Runtime）。
- **首次启动**：自动迁移旧版注册表设置、单账户 Token、`folder_sizes.sqlite3`。

## 3. 架构决策（含 anti-choice，改框架前先看这里）
| 决策 | 结论 | 原因 / 不选什么 |
|---|---|---|
| UI 框架 | PySide6 + qfluentwidgets (FluentWindow) | 便携 runtime 免装环境；不用 C#/WinUI（跨语言成本）、不用 Tkinter（太丑） |
| 网页登录 | 独立 pywebview 子进程，强制 Edge WebView2 + 私密配置 | 避免 Qt 与 WinForms 消息循环冲突；不使用 QtWebEngine（约 209 MiB），不回退 IE 内核 |
| 图床 AVIF | 上传线程调用外部 FFmpeg/libaom；默认 q70/speed 5 参考 AWJimage 公布预设 | 不移植 AGPL C++ 编码器、不捆绑来源不明的 FFmpeg；缺失时仅禁用预转换 |
| 资源监控/回收 | ctypes 调 Windows 进程 API + PDH GPU 计数器；后台空闲时 trim working set | 不引入 psutil/GPU 厂商 SDK；不在传输中回收，不宣称降低 private bytes |
| ModelScope 访问 | 官方 `modelscope-hub` SDK | 官方维护分页/上传；不自写 HTTP 协议 |
| 下载引擎 | 内置 aria2-next（RPC） | 多线程 + 断点续传 + 限速；SDK 自带下载单线程 |
| 同名下载恢复 | UI 记录覆盖策略；Runner 先做大小/SHA-256 预检，再决定复用、aria2 续传或重下 | 不把“同名”直接当“相同”，也不无条件浪费带宽覆盖 |
| 删除/移动/重命名 | 独立网页登录会话执行 | **平台限制：Token API 不允许删除**；批量删除 100 文件/次提交，失败回退逐项 |
| 上传 | 一律走有权 Token 的 SDK | 网页上传接口受限；文件夹上传、备份、图床共用 |
| 播放私有资源 | 仅监听 127.0.0.1 的内存流转发鉴权 | Token 不进播放器命令行，无媒体缓存 |
| Token / Cookie 存储 | 默认 DPAPI 加密 + `device.id` 设备绑定；实验模式可切换明文或关闭跨设备销毁 | 默认安全不变；降低保护前必须确认风险并完成四位数加法验证 |
| 运行环境 | 便携 `runtime/`（~220MB，不提交 Git） | 免装 Python；`main.py` 优先注入便携 site-packages |
| 上传限速 | monkey-patch SDK 的 `_CountedReadStream` | SDK 多线程上传，进程级共享限速器保证 aggregate 而非 per-file |
| 索引策略 | 启动全量一次；之后空闲/变更才刷新 | 避免普通翻页反复遍历仓库；后台间隔可配置 |
| 大文件上传 | 不设 50 GB 软件拦截，默认交给 SDK 上传 | 已用 51 GiB LFS 初始化请求确认服务端接受；内置 SDK 上限为 100 GB |
| 路径安全 | `normalize_remote_path` / `parse_modelscope_repository_url` / 下载越界校验 | 防 `..` 穿越与非法链接 |
| WebDAV 自定义树 | 命名虚拟根 + 文件夹 + source/target 挂载表；只读默认开启 | 同一源节点可重复组合，不复制远端数据；写操作仍回落到原仓库权限 |
| 远端压缩浏览 | 7z/ZIP/ISO 稀疏首尾 Range，TAR 逐 512B 头跳转，GZ/XZ 单流元数据 | 不支持 RAR；不以“浏览”为名下载完整大包 |

## 4. 目录地图
```
ModelScope-Manager/
├─ main.py                    入口：注入便携 site-packages → run()
├─ modelscope_manager/        核心包（59 个 Python 模块）
│  ├─ app.py                  应用初始化、run() 与旧版导入兼容层
│  ├─ app_helpers.py          应用级常量、路径/大小/速度格式化等纯函数
│  ├─ app_widgets.py          TransferChart、可拖放面包屑、仓库树/列表和拖放区
│  ├─ app_workers.py          上传、下载、缩略图、删除、备份、图床与索引后台线程
│  ├─ login_dialog.py         Edge WebView2 子进程的 Qt 控制对话框
│  ├─ page_shell.py           页面装配、导航注册与初始路由
│  ├─ page_resource.py / page_transfer.py / page_settings.py
│  ├─ page_search.py / page_backup.py / page_image_bed.py
│  ├─ page_about.py           Markdown 关于页：更新日志、开源库引用、支持者列表
│  ├─ page_webdav_mapping.py  WebDAV 命名虚拟树页面、编辑行为与持久化
│  │                          八个导航页面的控件构建与信号连接
│  ├─ main_mixins.py          MainWindow 行为组合入口；app.py 只继承这一稳定入口
│  ├─ main_accounts.py / main_backups.py / main_image_bed.py
│  ├─ main_integrations.py / main_transfers.py / main_window_shell.py
│  │                          账户、备份、图床、外部集成、传输及窗口生命周期行为
│  ├─ main_repository_browser.py  仓库连接、目录浏览与资源视图
│  ├─ main_repository_search.py   本地索引搜索与公共仓库搜索
│  ├─ main_url_import.py          HTTP(S) 临时下载 → 当前仓库目录上传
│  ├─ main_remote_actions.py      远端复制/移动/删除/播放与拖放上传
│  ├─ service.py              SDK 适配层：ModelScopeService / WebService / MultiAccountService，
│  │                          刻意保持 "GUI stays SDK-version agnostic"；含上传限速 monkey-patch
│  ├─ database.py             SQLite 数据层：AccountStore / IndexedEntry / everything 搜索
│  ├─ download_service.py     aria2 封装：DownloadSpec / Aria2Tuning / Aria2DownloadRunner
│  ├─ backup.py               备份任务：BackupJob / BackupStore（增量时间戳 / 同路径覆盖）
│  ├─ folder_index.py         FolderSizeIndex：目录聚合大小索引
│  ├─ webdav_server.py        ModelScopeWebDAV：纯 stdlib WebDAV 网关（AList V3 挂载）
│  ├─ webdav_mapping.py       自定义映射数据模型、路径校验与 JSON 序列化
│  ├─ remote_archive.py       7z/ZIP/TAR/ISO/GZ/XZ 远端 Range 目录探测
│  ├─ web_session.py          网页登录会话：身份 / 读取 / 删除（DELETE_BATCH_SIZE=100）
│  ├─ webview_login.py        Edge WebView2 登录子进程：私密会话、Cookie 捕获与 IPC
│  ├─ media_proxy.py          AuthenticatedMediaProxy：私有媒体内存流转发鉴权
│  ├─ player_installer.py     PotPlayer 可选安装：下载 7z → SHA-256 校验 → 7z-zstd 解压
│  ├─ plugin_installer.py     FFmpeg 插件：固定 ModelScope 包、校验、原子替换与自检
│  ├─ updater.py / main_updates.py  ModelScope 版本发现、下载校验、解压与退出后更新
│  ├─ security.py             DPAPI protect / unprotect（Token 设备绑定）
│  ├─ storage.py              路径常量 / DeviceIdentity / portable_settings（统一 data/ 目录）
│  ├─ http_security.py        modelscope_token_headers / safe_urlopen
│  ├─ image_bed.py            图床：ImageStore（年月目录 + 随机短前缀）
│  ├─ avif_converter.py       AVIF 参数模型、FFmpeg 探测/命令构建与临时文件转换
│  ├─ local_paths.py          本地文件迭代 / 上传源校验
│  ├─ localization.py         LocaleManager（中英即时切换）
│  ├─ transfer_policy.py      限速：SpeedRule / TransferPolicy / SharedRateLimiter
│  ├─ transfer_statistics.py  传输采样统计 / UploadHealthMonitor（TransferChart 数据源）
│  ├─ resource_monitor.py     本进程 CPU/内存/显存采样与 Windows working-set trim
│  ├─ startup.py              Windows 开机自启
│  ├─ styles.py               theme_qss（浅/深主题）
│  ├─ fluent_ui.py            自定 Fluent 控件（CleanComboBox 等）
│  ├─ locales/                zh_CN.json / en_US.json
│  └─ assets/                 check.svg 等
├─ embedded-tools/7zip-zstd/  7z.exe / 7z.dll（随仓库提交，供 PotPlayer 解压等）
├─ runtime/                   便携 Python 运行时（~220MB，**不提交 Git**）
├─ data/                      运行数据（**不提交 Git**）：settings.ini / device.id /
│                             public_pools.json / manager.sqlite3
├─ README.md                  对外门面（功能说明书）
├─ THIRD_PARTY_NOTICES.md     第三方组件声明
├─ CHANGELOG.md               统一版本日志入口
└─ V1.0.2~V1.0.5更新日志.md    历史版本详细日志
```

## 5. UI 地图（FluentWindow 左侧导航，设置固定底部）
| 页面 | 职责 | 位置 |
|---|---|---|
| 资源管理（默认） | 左：仓库树；右：文件列表、缩略图、标签与全盘搜索 | `page_resource.py` + `main_repository_browser.py` + `main_repository_search.py` + `main_remote_actions.py` |
| 资源搜索 | 粘贴公开链接浏览；Everything 风格搜索；搜索历史窗口 | `page_search.py` + `main_repository_search.py` + `main_accounts.py` |
| 传输列表 | 上传/下载二级栏；进度/速度/ETA；暂停/恢复/取消；统计 | `page_transfer.py` + `main_transfers.py` + `app_workers.py` |
| 备份文件夹 | 多任务、增量/覆盖模式及云端同步回本地 | `page_backup.py` + `main_backups.py` + `backup.py` |
| 图床 | 拖入/粘贴上传、AVIF 预转换、直链与本地记录 | `page_image_bed.py` + `main_image_bed.py` + `image_bed.py` + `avif_converter.py` |
| WebDAV 映射 | 命名入口、虚拟文件夹、重复节点挂载与只读策略 | `page_webdav_mapping.py` + `webdav_mapping.py` + `webdav_server.py` |
| 设置（底部固定） | 软件更新、账号、下载、播放、WebDAV、索引、外观与资源监控 | `page_settings.py` + `main_updates.py` + `main_accounts.py` + `main_integrations.py` + `main_window_shell.py` |
| 托盘菜单 | WebDAV 状态、实时速度、显示主窗与退出 | `main_window_shell.py` |

> 页面间关系：资源管理 → 选中文件 → 加入下载队列 → 传输列表；资源搜索 → 公开仓库 → 挂入 Public 根节点。`page_*.py` 只负责构建控件，行为落在对应 `main_*.py`，由 `main_mixins.py` 组合进 `MainWindow`。

## 6. 外部依赖（连接器四要素）
| 依赖 | 来源/安装 | 用途 | 缺失行为 |
|---|---|---|---|
| modelscope-hub | PyPI | SDK 读写仓库 | 无法访问远端 |
| PySide6 + PySide6-Fluent-Widgets | PyPI | UI | 无法启动 |
| pywebview 6.1 + WebView2 Runtime | PyPI + Windows 系统组件 | 网页登录与 Cookie 捕获 | 在线登录不可用；Token 登录不受影响 |
| FFmpeg（可选） | `embedded-tools/ffmpeg/ffmpeg.exe` 或系统 `PATH` | 图床 AVIF 预转换；要求 `libaom-av1` + AVIF muxer | 自动转换不可用；原图上传不受影响 |
| AWJimage | GitHub 设计参考，不进入发行包 | q70/speed 5 默认值与 libaom 对照参数 | 不影响运行；其 AGPL C++ 实现未复制 |
| aria2-next | 内置（便携构建） | 下载引擎（RPC） | 下载不可用 |
| 7z-zstd | `embedded-tools/`（随仓库） | 解压 PotPlayer 等 | PotPlayer 安装失败 |
| PotPlayer | **可选**：设置页下载安装（`ARXChem/Animations-List` 数据集 `! Software/PotPlayer.7z`） | 媒体播放 | 回退第三方播放器/系统默认 |
| `runtime/` | 便携构建（不提交 Git） | Python 运行时 | 需系统 Python 3.12 |

## 7. 核心数据（统一在 `data/`）
| 文件 | 内容 | 谁写 |
|---|---|---|
| `manager.sqlite3` | ★账户元数据、DPAPI 加密 Token、仓库缓存、文件元数据、目录大小索引 | database.py |
| `settings.ini` | QSettings 普通设置（主题/字号/语言/下载/播放/WebDAV、自定义映射、可选文件列、索引/AVIF 参数/资源回收阈值） | storage.py / page_webdav_mapping.py |
| `device.id` | 设备绑定标识（复制到别机 → 新 id → 清除 Token） | storage.py |
| `public_pools.json` | 公共资源池（资源搜索加载过的公开仓库，重启恢复） | public_pools.py |

- 迁移：旧版注册表设置、单账户 Token、`folder_sizes.sqlite3` 首次启动自动迁移进新结构。
- 秒搜直接查 `manager.sqlite3` 本地索引，不重复访问 ModelScope。
- 索引：启动全量一次；上传/备份/图床仅标记待更新，空闲约 5s 或切页后刷新。

## 8. 代码拆解（现状与维护边界）
**现状体检**
- 原 `app.py` 的 7521 行已按页面与行为拆分；当前入口保持为初始化/兼容层。新增页面继续使用独立 `page_*.py`，不把行为回填到入口。
- `app.py` 保留原有类、线程与辅助函数的导入兼容性；业务方法由 `main_mixins.py` 组合，外部调用方无需跟随内部路径迁移。
- 页面装配按七个导航页切成独立构建器；设置仍固定在底部，WebDAV 映射为顶部第六页、皮肤编号 6。

**依赖方向与维护纪律**
1. `app.py` → `main_mixins.py` → `page_*.py` / `main_*.py` → `service/database/...`，禁止业务模块反向导入 `app.py`。
2. 新页面控件放对应 `page_*.py`；页面行为放对应 `main_*.py`；长耗时任务放 `app_workers.py`；跨页控件放 `app_widgets.py`。
3. 单个职责文件以 1000 行为警戒线；超过后按行为边界继续拆分，不按行数机械切割。
4. 业务层扩展优先加小型数据模块（如 `webdav_mapping.py`），避免把序列化/校验塞进页面构建器。
5. `service/database/download_service/webdav_server` 等业务层只接受可独立测试的能力扩展。

## 9. 更新与发布
- **更新源**：公开数据集 `ARXChem/Software-List` 的 `ModelScope-Manager/<版本>.7z`；程序自动枚举并按数字版本选择最新版。
- **安装流程**：aria2-next 下载并按远端大小/SHA-256 校验 → 7z 路径安全检查与解压 → 用户确认重启 → 主进程退出后覆盖程序文件。归档禁止包含 `data/`。
- **现状**：无 CI / 无打包脚本提交；便携构建（runtime + start.bat，不含 data）手工产出；`CHANGELOG.md` 为统一入口，1.0.2—1.0.5 另保留详细日志。
- **建议**：
  1. 打包流程写成脚本或文档步骤（runtime 如何收集、data 如何排除、start.bat 生成），进仓库。
  2. 发布检查清单：`main.py` 可跑 → 便携构建自测 → 版本号 + 更新日志同步 → Tag。
  3. 本项目 Git 纪律：`runtime/`、`data/` 永不提交（.gitignore），`embedded-tools/` 随仓库。

## 10. 当前状态
- **版本**：v1.0.8；已完成多皮肤、传输历史/恢复、Windows 直挂、自定义 WebDAV 虚拟树、下载冲突恢复和多格式 Range 浏览。
- **发布目标**：GitHub Release `1.0.8`；ModelScope 更新资产为 `ModelScope-Manager/1.0.8.7z`。
- **结构**：`app.py` 为稳定兼容入口；七个导航页分别构建，自定义映射的数据模型与网关解析保持独立。
- **已知限制**（平台/设计约束，勿当 bug 修）：
  - Token 账户无法删除/移动/重命名（ModelScope 平台限制）→ 用网页会话。
  - 私有直链为 API 形式，仅对拥有仓库权限的访问者有效。
  - Git 仓库不保存空目录 → 新建目录首次上传后才可见。
  - 单文件低于 100 GB 可直接上传；达到内置 SDK 上限或平台限制时由 SDK 返回错误。
  - `dial tcp ... connectex` = AList 未连上网关（非程序 bug）。

## 11. 故障排查（先查这里再改代码）
| 症状 | 原因 / 处理 |
|---|---|
| WebDAV 根目录读取失败 `dial tcp connectex` | AList 未连到网关；同机用 `127.0.0.1`，其他设备选局域网/Docker 监听并放行防火墙 |
| 播放器打开私有资源无画面 | Token 不注入播放器；需先下载再播放，或确认端口仅 127.0.0.1 |
| 启动误载 qfluentwidgets | `runtime/` 缺失或不在 main.py 注入路径；检查便携 site-packages |
| Edge WebView2 登录窗口无法打开 | 安装/修复系统 WebView2 Runtime；检查 `pywebview`、`pythonnet` 与 `clr_loader` 是否在便携运行时 |
| AVIF 预转换不可用 | 检查 FFmpeg 是否可探测，并确认构建包含 `libaom-av1` encoder 与 AVIF muxer；不勾选时回退原图上传 |
| 显存显示不可用 | Windows/显卡驱动未提供 `GPU Process Memory` 计数器；CPU 与内存监控、自动工作集释放仍可用 |
| WebDAV 端口被占 | 自动切相邻端口，顶部横幅提示实际端口，写回设置页 |
| 备份/上传冲突 | 任务等下一轮（每 30s 检查）；避免同时大量操作 |
| 索引/文件夹大小不新 | 后台索引未完成；点右上角"更新索引"手动重建 |
| 缩略图缺失 | 失败格式回退普通图标，不无限重试；视频在 1.5s 定位取帧 |

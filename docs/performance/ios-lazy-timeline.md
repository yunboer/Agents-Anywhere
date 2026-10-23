# iOS 惰性时间线验证报告

验证日期：2026-10-24。范围：本 PR 的 `ChatTimelineView`、`TimelineHistoryPosition`
和对应回归测试；不涉及其他客户端或协议。

## 目的与实现边界

普通 `VStack` 会构建全部已加载的消息组。本次只将这一段改为原生
`LazyVStack`，用稳定 ID 注册滚动目标。历史提示、待发送消息和尾部可见性
探针保留在外层，避免这些状态依赖离屏视图的生命周期。

历史页插入后，原锚点可能离开惰性布局的渲染范围，因此先按 ID 定位，再按
测量差值恢复消息内部的阅读偏移。用户取消定位时，加载完成依据是数据已进入
展示层，不再等待离屏锚点产生几何回调。

虚拟化粒度是消息组。单条很长的 Markdown、展开的工具组及全量分组计算仍有
开销。本报告没有测得帧率、内存或主线程耗时改善，不将核心测试等同于 UI 性能验证。

## 环境

- Apple silicon，macOS 26.5.1。
- Command Line Tools，Apple Swift 6.3.2。
- 未安装完整 Xcode、iOS SDK 和 Simulator runtime。
- 临时编译产物和隔离测试副本存放于外置 APFS 卷，不提交缓存或锁文件。

## 检查结果

| 检查 | 结果 |
| --- | --- |
| 原始项目 `swift build --package-path ios` | 通过，核心源码构建成功 |
| 隔离环境完整 ClientCore 测试 | **251 项、31 个套件全部通过**，测试执行约 2.318 秒，不含构建时间 |
| 历史定位套件 | **11 项通过**，含本 PR 新增的 4 项 |
| 改动视图和测试文件的 Swift 语法解析 | 通过 |
| 新增滚动 API 的独立 SwiftUI 类型检查 | 通过；不等于完整视图或 App 编译 |
| `git diff --check` | 通过 |
| 完整 iOS App 构建、模拟器交互、性能对比 | 尚未执行 |

测试编译首次发现新增测试将 mutating 方法直接传给 `#expect`，宏展开时报
`cannot use mutating member on immutable value`。已改成先执行操作、保存结果再断言，
重新编译并通过上述完整测试。

### Command Line Tools 隔离环境

原始 `swift test` 因工具链缺少 `Testing` 模块无法运行。本轮复制 `ios/` 到临时
目录，仅在副本的 `Package.swift` 添加 Swift 官方测试框架依赖：

- `swiftlang/swift-testing`：`swift-6.3.2-RELEASE`，revision
  `70eff261d7f462cad1fff51e05bcc74aa0b0f420`。
- 解析得到 `swiftlang/swift-syntax` 603.0.2，revision
  `79e4b74a295b6eb74a8b585e3a39d29e70c1dbd1`。
- CLT 同时缺少 `_TestingInterop`；从上述同版本官方源文件
  `Sources/_TestingInterop/FallbackEventHandler.swift` 编译临时动态库，并通过
  `-Xlinker -L...` 和 `-rpath` 供此测试进程使用。没有替换系统库。

运行前比对副本与仓库的 Swift 源码和测试文件，除临时 manifest 外内容一致。
正式项目没有新增依赖。此结果证明 macOS 上 ClientCore 测试通过，仍需在完整
Xcode 环境用下列标准命令验证 iOS 构建。

## 历史定位覆盖场景

`ios/Tests/ClientCoreTests/TimelineHistoryPositionTests.swift` 共 11 个测试，
其中本 PR 新增 4 个：

| 场景 | 检查内容 |
| --- | --- |
| 请求先于展示完成 | 旧行仍在展示时不能提前结束加载 |
| 长消息内阅读 | 前插历史后保留消息内部的偏移 |
| 流式尾部增长、重复布局 | 不把尾部增高算作前插增高，不累计重复修正 |
| 新手势、导航取消 | 取消后不能覆盖新的阅读意图 |
| 未变化、空页、失败页 | 不移动阅读位置，正常结束加载 |
| 顶部过度拖拽 | 恢复至静止 inset，而非保留过度滚动量 |
| 工具组前缀合并 | 保留原组尾部边界 |
| 惰性锚点重建（新增） | 每个展示页只请求一次 ID 定位，仍等待有效布局 |
| 取消后的离屏锚点（新增） | 没有几何回调也能在展示完成后结束 |
| 没有测量锚点（新增） | 请求和展示以任意顺序完成均能结束 |
| 估算坐标未变化（新增） | ID 定位后仍恢复消息内偏移，重复测量不再修正 |

## 标准复现命令

在已安装兼容 Xcode 的 Mac 上，从仓库根目录运行：

```sh
swift build --package-path ios
swift test --package-path ios --filter TimelineHistoryPositionTests
swift test --package-path ios
xcodebuild -project 'ios/Agents Anywhere/Agents Anywhere.xcodeproj' \
  -scheme 'Agents Anywhere' -configuration Debug \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath ios/.build/simulator CODE_SIGNING_ALLOWED=NO build
```

仅检查语法时可以运行下列命令；它不会验证 UIKit/SwiftUI 视图完整类型和链接：

```sh
swiftc -frontend -parse \
  'ios/Agents Anywhere/Agents Anywhere/Views/Chat/ChatTimelineView.swift' \
  ios/Tests/ClientCoreTests/TimelineHistoryPositionTests.swift
```

## 待执行的 UI 与性能验证

用相同设备、构建配置和构造会话比较基线与 PR。逐次加载至 100、500、1,000 条
记录，混合长 Markdown、代码块、图片和工具组，记录打开耗时、内存及滚动卡顿。
通过 Instruments 检查离屏 Markdown 布局工作与主线程耗时。保留原始 trace，
再报告差异；模拟器结果也不能直接作为真机帧率结论。

交互检查包括：长消息内前插历史、工具组合并、加载中拖离/打开侧栏、失败与最终
历史页、到底部、跟随流式回复、主动上滑后停止跟随、折叠状态恢复、键盘、旋转和
Dynamic Type。上述检查尚未执行；PR 保持 Draft。

仓库现有 CI 不包含 iOS 构建或测试，因此该 CI 的通过/跳过不能替代本节验证。

## 隔离测试的复现方法

以下命令用于只有相同 CLT 的环境；有完整 Xcode 时优先使用上面的标准命令。
从仓库根目录执行，先将 `VALIDATION_DIR` 设为一个新的临时目录，路径不要含空格：

```sh
export VALIDATION_DIR=/path/to/new-validation-directory
mkdir -p "$VALIDATION_DIR/ios" "$VALIDATION_DIR/support"
rsync -a --exclude .build ios/ "$VALIDATION_DIR/ios/"
python3 - <<'PY'
import os
from pathlib import Path
p = Path(os.environ["VALIDATION_DIR"]) / "ios/Package.swift"
s = p.read_text().replace(
    '    targets: [',
    '    dependencies: [.package(url: "https://github.com/swiftlang/swift-testing.git", '
    'revision: "70eff261d7f462cad1fff51e05bcc74aa0b0f420")],\n    targets: [', 1
).replace(
    'dependencies: ["ClientCore"],',
    'dependencies: ["ClientCore", .product(name: "Testing", package: "swift-testing")],'
)
p.write_text(s)
PY
swift package --package-path "$VALIDATION_DIR/ios" \
  --scratch-path "$VALIDATION_DIR/build" resolve
swiftc -emit-library -parse-as-library -package-name swift_testing \
  -module-name _TestingInterop -target arm64-apple-macosx15.0 \
  "$VALIDATION_DIR/build/checkouts/swift-testing/Sources/_TestingInterop/FallbackEventHandler.swift" \
  -o "$VALIDATION_DIR/support/lib_TestingInterop.dylib"
swift test --package-path "$VALIDATION_DIR/ios" \
  --scratch-path "$VALIDATION_DIR/build" \
  -Xlinker -L"$VALIDATION_DIR/support" \
  -Xlinker -rpath -Xlinker "$VALIDATION_DIR/support"
```

SwiftSyntax 解析版本应与上述记录一致；若不同，应记录实际版本，不能视为同一环境。

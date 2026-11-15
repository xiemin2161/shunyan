# macOS 原生适配与实机验收说明

## 键盘事件实现

`host/adapters/macos_keyboard.py` 通过 `ctypes` 调用 CoreGraphics 的 `CGEventSourceCreate`、`CGEventCreateKeyboardEvent` 和 `CGEventPost`。适配器使用 macOS 虚拟键码，将跨平台语义键名映射为 Mac 键位；界面应把 `ALT` 显示为 Option，把 `META` 显示为 Command。字母和标点映射代表虚拟键码位置，输入法/键盘布局可能影响最终字符。

组合键按配置顺序依次按下，按相反顺序释放；一到五个键位、主键位于最后，并拒绝未知键。接口包括 `press_key`、`release_key`、`tap`、`release_all` 和 `permission_status`。适配器在导入和构造时不弹系统授权提示。每次发送新的按下事件前通过 `CGPreflightPostEventAccess` 检查权限；未授权或系统缺少可用权限探测接口时不声称已发送成功，并返回结构化错误。

Python 脚本的系统权限归属于启动它的宿主身份。开发调试时可能需要为实际启动服务的终端授权；正式交付应以稳定签名的宿主 App/辅助程序身份请求授权，并在升级时保持签名身份一致。`request_permission()` 会请求系统键盘事件授权，服务端只能在用户显式点击授权入口后调用。不得通过模拟点击系统设置或绕过 TCC 授权。

## Mac 实机验收步骤

以下步骤必须在 Mac 上实际完成；Windows 上的代码阅读不能替代：

1. 在 Intel Mac 和 Apple Silicon Mac 上分别安装/启动已签名的本机宿主，核对打包架构、CoreGraphics 动态库加载及日志中没有密钥、令牌或录音内容。
2. 首次未授权启动时确认状态 API 返回 `PLATFORM_PERMISSION_REQUIRED`，并且键盘事件没有投递；在系统设置中为正确的签名宿主开启必要的辅助功能/键盘事件权限，再重启服务确认权限状态变化。
3. 分别验证 Option/Alt 左右键、Control、Shift、Command 左右键、Enter、Esc 的单键按下/释放；确认键正常输入，取消键行为符合映射。
4. 使用一个可观察的组合键（例如 `CTRL_LEFT` + `SHIFT_LEFT` + `P`）验证按下与逆序释放；重复、取消、服务退出和手机断开时检查没有粘滞修饰键。
5. 在不授予权限、撤销权限、切换到锁屏/快速用户切换等场景重新连接，确认状态屏和 API 有明确降级响应，不报告虚假的成功。
6. 记录 macOS 版本、芯片架构、宿主签名标识、授予的系统权限、按键映射结果和断线释放结果，作为发布验收证据。

## 当前限制

- 当前开发工作站运行 Windows；无法在此执行 macOS 框架加载、TCC 授权、签名/公证、Intel 或 Apple Silicon 打包，也没有 Mac 实机事件投递证据。
- `CGPreflightPostEventAccess` 仅用于探测，不主动弹授权对话框。未来设置界面需要由用户显式点击“请求系统授权”后，再由受签名宿主调用系统授权请求 API，并提供“打开系统设置”的可见指引。
- macOS 键盘适配不包含音频设备枚举、虚拟麦克风驱动安装或音频传输；这些能力仍按 AI 开发计划的独立音频里程碑进行实机验证。

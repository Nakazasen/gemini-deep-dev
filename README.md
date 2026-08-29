# Gemini Deep Dev

> Một lớp kiểm chứng cho Gemini trong Antigravity: thử thay đổi ở nơi cách ly, chạy test độc lập, chỉ đưa vào dự án thật khi đạt yêu cầu.

**Phiên bản phát hành hiện tại: v0.1.0**

Gemini Deep Dev không biến Gemini thành một mô hình khác. Nó làm cho các thay đổi mã nguồn của Gemini đáng tin hơn bằng cách tách việc **đề xuất** khỏi quyền **áp dụng** thay đổi.

## Khi nào nên dùng?

- Dùng Gemini bình thường cho hỏi đáp, đọc code, rà soát, giải thích, `git status`, hoặc việc nhanh không sửa dự án.
- Gõ **`/deep-dev`** khi muốn Gemini tạo/sửa/xóa file mã nguồn hay cấu hình quan trọng.

Chế độ Deep chỉ được kích hoạt bởi `/deep-dev`; nó không khóa mọi thao tác của Gemini trong Antigravity.

## Luồng hoạt động

```text
Bạn gõ /deep-dev
       │
       ▼
Gemini khảo sát chỉ-đọc và lập đề xuất có scope rõ ràng
       │
       ▼
Deep Dev tạo worktree cách ly, áp dụng đề xuất tại đó
       │
       ▼
Harness chạy các test đã allowlist, độc lập với Gemini
       │
       ├── Có lỗi ──► rollback: dự án thật không đổi
       │
       └── Tất cả đạt ──► apply delta vào dự án thật + refresh evidence
```

Khi test thất bại, Deep Dev có thể tạo tối đa hai repair ticket có scope hẹp. Với vấn đề phức tạp, `/teamwork-preview` chỉ đóng vai trò tư vấn đọc-độc-lập; nó không có quyền ghi trực tiếp hay tự quyết định kết quả.

## Điều kiện an toàn

- Không có test đạt thì không áp dụng bản vá.
- Test chỉ chạy từ allowlist trong `.deep_dev/config.json` của từng dự án.
- Ticket có scope và dùng một lần; không được dùng lại để mở rộng phạm vi.
- Worktree thử nghiệm bị dọn khi rollback.
- Hook chỉ nghiêm trong phiên `/deep-dev`; không phải một tường lửa chặn Gemini mọi lúc.

## Cài đặt trên Windows

Yêu cầu: Windows, Python Launcher (`py`) và Antigravity đã được đóng hoàn toàn.

```powershell
git clone https://github.com/Nakazasen/gemini-deep-dev.git
Set-Location .\gemini-deep-dev
.\tools\Install-DeepDev.ps1 -ManifestUrl 'https://raw.githubusercontent.com/Nakazasen/gemini-deep-dev/main/release/update.json'
```

Bộ cài sẽ:

1. Kiểm hash SHA-256 của bundle trước khi cài.
2. Sao lưu `hooks.json` hiện có theo timestamp.
3. Cài skill, MCP harness và descriptor vào hồ sơ Windows hiện tại (`%USERPROFILE%\.gemini`).
4. Ghi trạng thái cài đặt vào `%USERPROFILE%\.gemini\config\deep-dev-update.json`.

Sau khi cài hoặc cập nhật, **đóng hoàn toàn rồi mở lại Antigravity**.

Không cần gỡ cài đặt bản cũ: installer dùng thư mục staging và chỉ thay thế khi bundle đã qua kiểm tra. Nếu cần gỡ, hãy tắt/xóa riêng object `deep-dev-enforcement` trong `%USERPROFILE%\.gemini\config\hooks.json`, sau đó xóa các thư mục Deep Dev đã cài.

## Cách dùng trong Antigravity

Trong ô chat:

```text
/deep-dev
Thêm chức năng X. Chỉ được đổi các file A, B. Chạy test Y.
```

Ở cuối lượt, Gemini phải báo `run ID`, trạng thái (`ACCEPT_PATCH`, `ROLLBACK` hoặc `BLOCKED`), đường dẫn patch, kết quả test, và evidence path. Chỉ `ACCEPT_PATCH` mới nghĩa là thay đổi đã vào workspace thật.

## Cập nhật

Auto-update tắt mặc định. Sau khi phát hành bản ZIP lên GitHub Releases, bạn có thể bật tác vụ kiểm tra mỗi ngày:

```powershell
.\tools\Enable-AutoUpdate.ps1 -ManifestUrl 'https://raw.githubusercontent.com/Nakazasen/gemini-deep-dev/main/release/update.json'
```

Updater chỉ cài bản mới hơn khi file ZIP tải về khớp SHA-256 trong manifest. Cập nhật xong vẫn cần khởi động lại Antigravity.

## Phát hành bản mới

```powershell
# Cập nhật VERSION trước, sau đó:
.\tools\Build-Release.ps1
py -3 -m unittest -v tests\test_update_simulation.py
```

Lệnh build tạo `dist/gemini-deep-dev-vX.Y.Z.zip` và cập nhật `release/update.json`. Tạo GitHub Release đúng tag `vX.Y.Z`, tải ZIP lên release, rồi commit manifest đã cập nhật.

## Kiểm thử

```powershell
py -3 -m unittest -v tests\test_update_simulation.py
```

Bộ test mô phỏng một update v0.1.0 → v0.1.1 từ URL cục bộ, kiểm tra bundle được cài và cấu hình phiên bản được nâng lên. Một test riêng xác nhận gói có SHA-256 sai bị từ chối.

## Cấu trúc repo

```text
bundle/     Bản cài có thể kiểm hash: skill, hook, MCP harness và descriptor
tools/      Installer, updater, build release và công cụ tạo integrity registry
release/    Manifest mà updater đọc để biết bản phát hành mới nhất
tests/      Kiểm thử giả lập luồng cập nhật
```

## Trạng thái v0.1.0

v0.1.0 đóng gói một bản cài Deep Dev hoạt động theo mô hình opt-in: Gemini thường không bị chặn; `/deep-dev` mới bật rào kiểm chứng, worktree isolation, test allowlist và rollback fail-closed.

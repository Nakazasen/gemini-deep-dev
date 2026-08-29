# Gemini Deep Dev v0.1.0

Gemini Deep Dev là bộ giáp an toàn dành cho Gemini trong Antigravity khi bạn giao việc sửa mã nguồn.

Gemini vẫn làm việc nhanh như bình thường. Chỉ khi bạn gõ `/deep-dev`, luồng kiểm chứng nghiêm ngặt mới được bật:

1. Gemini chuẩn bị đề xuất thay đổi, chưa sửa dự án thật.
2. Deep Dev tạo một worktree cách ly để thử bản vá.
3. Harness chạy các lệnh test đã được cho phép độc lập với Gemini.
4. Test đạt thì mới áp dụng bản vá; có lỗi thì hủy bản thử và giữ dự án thật nguyên vẹn.

## Thành phần trong repo

- `bundle/`: bản cài hoàn chỉnh gồm skill, hook, MCP harness và descriptor MCP.
- `tools/Install-DeepDev.ps1`: cài Deep Dev vào hồ sơ người dùng Windows hiện tại.
- `tools/Enable-AutoUpdate.ps1`: bật kiểm tra cập nhật tự động mỗi ngày.
- `tools/Build-Release.ps1`: tạo gói ZIP phát hành và manifest cập nhật.
- `tests/test_update_simulation.py`: kiểm thử giả lập cập nhật, gồm kiểm tra chữ ký SHA-256.

## Cài đặt

Mở PowerShell tại thư mục repo rồi chạy:

```powershell
.\tools\Install-DeepDev.ps1
```

Sau khi cài hoặc cập nhật, đóng hoàn toàn và mở lại Antigravity.

## Cập nhật tự động

Mặc định cập nhật tự động **tắt**. Điều này tránh việc một bản phát hành chưa được chuẩn bị đầy đủ tự thay đổi môi trường của bạn.

Sau khi đã tạo GitHub Release và tải lên file ZIP tương ứng, bật lịch kiểm tra hằng ngày bằng:

```powershell
.\tools\Enable-AutoUpdate.ps1 -ManifestUrl 'https://raw.githubusercontent.com/Nakazasen/gemini-deep-dev/main/release/update.json'
```

Updater chỉ cài bản mới khi manifest chỉ tới phiên bản cao hơn và SHA-256 của gói tải về khớp chính xác.

## Phát hành bản mới

1. Cập nhật `VERSION`.
2. Chạy `.\tools\Build-Release.ps1`.
3. Tạo GitHub Release theo tag phiên bản và tải file trong `dist/` lên release đó.
4. Commit manifest `release/update.json` đã được tạo lại.

## Kiểm thử

```powershell
py -3 -m unittest -v tests\test_update_simulation.py
```

Bộ kiểm thử tạo gói phát hành giả v0.1.1, cập nhật từ URL cục bộ đã kiểm hash, rồi xác nhận bản cài và cấu hình update đã được nâng cấp. Một test riêng xác nhận gói có SHA-256 sai bị từ chối.

# Gemini Deep Dev v0.1.0

Bộ giáp an toàn cho **Gemini trong Antigravity** khi bạn giao việc sửa code.

Bình thường Gemini Flash vẫn làm việc nhanh. Chỉ khi bạn gõ `/deep-dev`, nó mới chuyển sang chế độ kiểm tra nghiêm ngặt:

1. Gemini đề xuất code, nhưng chưa được sửa dự án thật.
2. Deep Dev thử thay đổi đó trong một bản sao cách ly.
3. Hệ thống chạy test độc lập.
4. Pass thì mới áp dụng; fail thì hủy bản thử, dự án thật giữ nguyên.

Project này chứa bản cài thật: skill, hook, MCP harness, installer Windows và kiểm thử update. Nó hiện phục vụ Gemini/Antigravity, không phải một framework chung cho mọi AI.

## Khi nào dùng

- Việc nhanh, ít rủi ro: dùng Gemini bình thường.
- Sửa nhiều file, logic quan trọng hoặc muốn có bằng chứng test: gõ `/deep-dev`.

## Quy ước

- Không đặt dữ liệu dự án thật, log nhạy cảm hoặc credential vào repo.
- `ACCEPT_PATCH` chỉ được tin khi có artifact test của harness.
- `/teamwork-preview` chỉ tư vấn khi repair; harness độc lập quyết định pass/fail.

## Cài đặt

```powershell
Set-Location 'D:\Sandbox\gemini-deep-dev'
.\tools\Install-DeepDev.ps1
```

Installer sao lưu `hooks.json`, cài skill + harness, rồi chỉ đăng ký một lệnh `/deep-dev`. Khởi động lại hoàn toàn Antigravity sau khi cài.

## Auto-update

Sau khi tạo GitHub Release, cập nhật `release/update.json` bằng URL HTTPS của file ZIP và SHA-256 tương ứng. Sau đó bật kiểm tra cập nhật hằng ngày:

```powershell
.\tools\Enable-AutoUpdate.ps1 -ManifestUrl 'https://raw.githubusercontent.com/Nakazasen/gemini-deep-dev/main/release/update.json'
```

Updater tải ZIP vào thư mục tạm, kiểm tra SHA-256 rồi mới tạo backup/cài. Test giả lập bằng `file://` xác nhận cả nhánh update thành công lẫn nhánh hash sai bị từ chối:

```powershell
py -3 -m unittest -v tests\test_update_simulation.py
```

## Tạo release mới

1. Đổi `VERSION` và `bundle/VERSION`.
2. Chạy `./tools/Build-Release.ps1`.
3. Upload ZIP trong `dist/` vào GitHub Release cùng version.
4. Commit file `release/update.json` đã nhận SHA-256 mới.

## Bắt đầu Git

Sau khi tạo repository `gemini-deep-dev` trên GitHub, mở PowerShell tại thư mục này và chạy:

```powershell
git init
git branch -M main
git remote add origin <URL-repository-của-bạn>
git add README.md
git commit -m "chore: initialize Gemini Deep Dev"
git push -u origin main
```

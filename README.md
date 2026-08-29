# Gemini Deep Dev

Gemini Deep Dev giúp Gemini sửa code đáng tin hơn trong Antigravity.

Khi bạn dùng Gemini bình thường, nó vẫn nhanh và không bị Deep Dev cản trở. Khi bạn gõ **`/deep-dev`**, mọi thay đổi sẽ đi qua một vòng kiểm tra an toàn trước khi vào dự án thật.

## Nó làm gì?

```text
Bạn yêu cầu thay đổi
        ↓
Gemini lập kế hoạch và đề xuất bản vá
        ↓
Deep Dev thử bản vá trong thư mục cách ly
        ↓
Chạy test độc lập
        ↓
Đạt: áp dụng vào dự án thật   |   Lỗi: hủy bản thử, dự án thật không đổi
```

Điểm quan trọng: Gemini không thể tự nói “test ổn”. Harness tự chạy những test mà dự án đã cho phép, rồi mới quyết định áp dụng hay rollback.

## Cài đặt

Đóng hoàn toàn Antigravity, mở PowerShell và chạy:

```powershell
git clone https://github.com/Nakazasen/gemini-deep-dev.git
Set-Location .\gemini-deep-dev
.\tools\Install-DeepDev.ps1
```

Xong thì mở lại Antigravity. Không cần gỡ bản cũ: installer tự sao lưu hook và thay bản cài theo cách an toàn.

## Dùng hằng ngày

- Việc nhanh, hỏi đáp, đọc code: dùng Gemini như bình thường.
- Muốn thay đổi code có kiểm chứng: gõ `/deep-dev`, rồi mô tả việc cần làm và test cần chạy.

Ví dụ:

```text
/deep-dev
Thêm chức năng xuất CSV. Chỉ sửa các file được nêu trong proposal và chạy toàn bộ test hiện có.
```

Nếu kết quả là `ACCEPT_PATCH`, thay đổi đã được áp dụng. Nếu là `ROLLBACK`, Deep Dev đã chặn bản thử lỗi; dự án thật vẫn nguyên vẹn.

## Tự cập nhật

Tự cập nhật được **bật mặc định**. Hệ thống kiểm tra sau khi bạn đăng nhập Windows; không cần quyền quản trị máy.

Chỉ khi có bản mới hơn và file tải về khớp SHA-256 công bố, Deep Dev mới tự cài. Nếu vừa có cập nhật, hãy đóng và mở lại Antigravity để nạp bản mới.

## An toàn

- Deep Dev chỉ nghiêm khi bạn chủ động gọi `/deep-dev`.
- Bản vá luôn được thử ở worktree cách ly trước.
- Test fail thì rollback; không ghi dở dang vào dự án thật.
- Nếu cần sửa sau test fail, số vòng repair bị giới hạn để tránh lặp vô tận.
- `/teamwork-preview` chỉ có thể hỗ trợ phân tích; không có quyền tự ghi code hay tự duyệt kết quả.

## Dành cho người đóng góp

Mã nguồn gồm bundle cài đặt, updater, harness và test mô phỏng update. Các hướng dẫn phát hành nội bộ nằm trong mã nguồn, không cần cho người dùng cuối thực hiện.

---
name: deep-dev
description: Run the high-performance Boosted Deep Dev workflow with deep reasoning, evidence-based verification, self-healing reflection loops, and mandatory AgentMemory checkpoints. Activate when the user invokes /deep-dev or requests deep implementation, refactoring, or bug fixes.
---

# Boosted Deep Dev Engine (v0.2.0)

Thực thi quy trình lập trình sâu, tất định và tự chữa lành cho mọi tác vụ code trên Antigravity IDE:

## 1. Tư duy sâu & Phân tích ngữ cảnh (Deep Reasoning & AST First)
- Đọc và phân tích codebase có chọn lọc (dùng Graphify/grep_search/view_file).
- Nắm bắt đúng AST và dependency graph trước khi chạm vào mã nguồn.
- Lập kế hoạch rõ ràng cho các thay đổi phức tạp trước khi thực hiện.

## 2. Thực thi trực tiếp & Chống làm ẩu (Zero-Tolerance for Placeholders)
- Sử dụng trực tiếp các công cụ chỉnh sửa chuẩn (`replace_file_content`, `write_to_file`) mà không bị rào cản hay gián đoạn luồng.
- Tuyệt đối KHÔNG viết code giữ chỗ hoặc lười biếng (`// TODO`, `/* code giữ nguyên */`, `...`, `pass`).
- Mọi đoạn code phải hoàn chỉnh 100%, có type hints và xử lý ngoại lệ chặt chẽ.

## 3. Vòng lặp tự động sửa lỗi (Self-Healing / Reflection Loop)
- Sau khi chỉnh sửa code, BẮT BUỘC chạy ngay kiểm thử thực tế qua terminal (`run_command`):
  - Chạy test tự động: `py -3 -m pytest <test_files> -q`
  - Kiểm tra cú pháp toàn dự án: `py -3 -m compileall src -q`
  - Kiểm tra diff formatting: `git diff --check`
- Nếu phát hiện lỗi (Exception, SyntaxError, AssertionError):
  - Không dừng lại phân bua.
  - Tự động đọc lại traceback -> Tìm nguyên nhân gốc -> Sửa file -> Chạy lại test đến khi 100% xanh (tối đa 3 vòng lặp).

## 4. Kiểm chứng trung thực (Evidence-based Verification)
- Tuyệt đối không báo cáo "Đã test PASS" hoặc "Đã fix thành công" nếu không có bằng chứng thực thi thực tế (stdout/stderr từ terminal runner).

## 5. Lưu Checkpoint AgentMemory
- Sau mỗi milestone quan trọng hoàn thành, tự động lưu checkpoint vào `AgentMemory` để duy trì ngữ cảnh liên tục cho dự án.


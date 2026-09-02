---
name: deep-dev
description: Run the high-performance Boosted Deep Dev workflow with Lead Architect, Coder Agent, and Critic/QA Sub-Agent for strict separation of implementation and verification. Activate when the user invokes /deep-dev or requests deep implementation, refactoring, or bug fixes.
---

# Boosted Deep Dev Engine (v0.3.0 - Dual-Agent Edition)

Thực thi quy trình lập trình sâu, tất định và tự chữa lành với sự phân tách độc lập giữa luồng Thực thi (Coder) và Đối soát (Critic/QA):

## 1. Vai trò 1: Lead Architect (Tư duy sâu & Phân tích ngữ cảnh)
- Đọc và phân tích codebase có chọn lọc (dùng Graphify/grep_search/view_file).
- Nắm bắt đúng AST và dependency graph trước khi chạm vào mã nguồn.
- Lập kế hoạch kiến trúc và phân rã các bước thực thi rõ ràng.

## 2. Vai trò 2: Coder Sub-Agent (Thực thi & Chống làm ẩu)
- Sử dụng trực tiếp các công cụ chỉnh sửa chuẩn (`replace_file_content`, `write_to_file`).
- Tuyệt đối KHÔNG viết code giữ chỗ hoặc lười biếng (`// TODO`, `/* code giữ nguyên */`, `...`, `pass`).
- Mọi đoạn code phải hoàn chỉnh 100%, có type hints và xử lý ngoại lệ chặt chẽ.
- Bàn giao toàn bộ mã nguồn cho Critic Sub-Agent để thẩm định độc lập.

## 3. Vai trò 3: Critic Sub-Agent (Đối soát & Thẩm định độc lập)
- Chạy kiểm thử độc lập trực tiếp qua terminal (`run_command`):
  - Chạy test tự động: `py -3 -m pytest <test_files> -q`
  - Kiểm tra cú pháp toàn dự án: `py -3 -m compileall src -q`
  - Kiểm tra diff formatting: `git diff --check`
- Rà soát đối nghịch (Adversarial Review): Quét rủi ro bảo mật, edge cases, hồi quy logic.
- Vòng lặp phản hồi (Reflection Loop): Nếu phát hiện lỗi (Exception, SyntaxError, AssertionError), gửi trả lại traceback để Coder sửa lại đến khi 100% xanh (tối đa 3 vòng lặp).

## 4. Kiểm chứng trung thực (Evidence-based Verification)
- Tuyệt đối không báo cáo "Đã test PASS" hoặc "Đã fix thành công" nếu không có bằng chứng thực thi thực tế (stdout/stderr từ terminal runner).

## 5. Lưu Checkpoint AgentMemory
- Sau mỗi milestone quan trọng hoàn thành và được Critic thông qua, tự động lưu checkpoint vào `AgentMemory` để duy trì ngữ cảnh liên tục cho dự án.


# Gemini Deep Dev (v0.1.1)

**Bộ khung thực thi tất định (Deterministic Execution Harness & Quality Gates) dành riêng cho Google Gemini trong Antigravity IDE.**

---

## 🎯 Vấn đề giải quyết

Dòng mô hình **Gemini Flash (1.5 / 2.0 / 3.7 Flash)** có tốc độ cực nhanh và cửa sổ ngữ cảnh lớn, nhưng khi lập trình tự do thường gặp phải các vấn đề:
1. **Báo PASS ảo (Fake PASS)**: Khẳng định đã làm xong hoặc đã fix lỗi nhưng thực tế chưa chạy lệnh test/compiler nào.
2. **Lười biếng (Placeholder Syndrome)**: Tự động rút gọn code bằng `// TODO`, `/* code giữ nguyên */`, `...`, `pass`.
3. **Loãng ngữ cảnh (Attention Dispersion)**: Khi nạp nhiều file cùng lúc, model bị phân tán sự chú ý và bỏ sót các ràng buộc nhỏ.

**Gemini Deep Dev** ra đời để biến Gemini Flash thành một cỗ máy lập trình **nhanh, chuẩn xác và không thể gian lận** bằng các chốt chặn ở tầng thực thi (Runtime Process Gates).

---

## ⚙️ Cơ chế hoạt động

```text
               Người dùng yêu cầu tác vụ (/deep-dev)
                               ↓
               Khảo sát AST & Scoped Subgraph (Graphify)
                               ↓
               Sinh bản vá nguyên tử (Atomic Exact Replace - No Placeholders)
                               ↓
               Thử nghiệm bản vá trong Git Worktree cách ly
                               ↓
               Chạy Test Suite & Linter độc lập từ Terminal
                               ↓
      ┌────────────────────────┴────────────────────────┐
      ▼                                                 ▼
[TẤT CẢ TEST PASS]                              [CÓ TEST FAIL / LỖI]
  • Tự động merge vào dự án thật                  • Hủy bỏ bản thử (Rollback an toàn)
  • Cập nhật AgentMemory Checkpoint               • Dự án gốc giữ nguyên 100%
  • Báo cáo trạng thái ACCEPT_PATCH               • Kích hoạt Repair Feedback Loop
```

---

## 🚀 Tính năng nổi bật trong bản v0.1.1 (Lean & Fast)

- **Lean Execution Engine**: Tinh giản tối đa các bước thủ tục trung gian rườm rà, giải phóng 100% năng lực suy luận của Gemini Flash cho code logic.
- **Zero-Evidence = Failure**: Cấm tuyệt đối việc báo cáo hoàn thành nếu không có stdout thực tế từ Terminal Runner.
- **Atomic Diff Interceptor**: Ép buộc thay thế chính xác từng dòng code (`exact_replace`), tự nhiên loại bỏ hoàn toàn code lười `TODO`.
- **Chế độ kép linh hoạt (Dual Mode)**:
  - **Chat thông thường**: Hoạt động nhanh, áp dụng quy tắc Lean Invariant (code đầy đủ, kiểm thử trước khi báo cáo).
  - **Lệnh `/deep-dev`**: Kích hoạt toàn bộ quy trình kiểm chứng an toàn qua Git Worktree độc lập, Graphify AST và AgentMemory.

---

## 📦 Cài đặt

1. Đóng hoàn toàn Antigravity IDE.
2. Mở PowerShell và chạy:

```powershell
git clone https://github.com/Nakazasen/gemini-deep-dev.git
Set-Location .\gemini-deep-dev
.\tools\Install-DeepDev.ps1
```

3. Mở lại Antigravity IDE. Bộ công cụ sẽ tự động tích hợp vào hệ thống (`skills`, `hooks`, `mcp`).

---

## 💡 Hướng dẫn sử dụng

### 1. Dùng hằng ngày
- Hỏi đáp, đọc hiểu mã nguồn, phân tích lỗi: Sử dụng Gemini như bình thường.
- Sửa code nhanh: Gemini sẽ tự động dùng atomic diff và chạy test kiểm chứng.

### 2. Khi cần thay đổi kiến trúc hoặc tính năng quan trọng
Gõ lệnh **`/deep-dev`** kèm yêu cầu:

```text
/deep-dev
Thêm middleware xác thực JWT và bảo vệ các private routes. Chạy test suite để kiểm chứng trước khi áp dụng.
```

- Nếu kết quả là **`ACCEPT_PATCH`**: Bản vá đã vượt qua 100% bài test và đã được áp dụng vào dự án.
- Nếu kết quả là **`ROLLBACK`**: Bản thử nghiệm không đạt chuẩn đã bị chặn lại; mã nguồn của bạn hoàn toàn an toàn.

---

## 🔄 Cơ chế tự động cập nhật

- Tính năng tự cập nhật được **bật mặc định**.
- Hệ thống tự động kiểm tra bản cập nhật mới nhất từ GitHub khi người dùng đăng nhập Windows (không yêu cầu quyền Administrator).
- Tự động đối soát mã băm SHA-256 trước khi áp dụng bản nâng cấp.

---

## 🛠️ Dành cho nhà phát triển

```powershell
# Chạy toàn bộ test suite
py -3 -m pytest tests
py -3 -m pytest bundle/deep-dev/scripts/test_deep_dev_security.py

# Đóng gói bản phát hành mới
.\tools\Build-Release.ps1
```

---

## 📄 Bản quyền

Phát hành dưới giấy phép MIT License. Bản quyền thuộc về [Nakazasen](https://github.com/Nakazasen).

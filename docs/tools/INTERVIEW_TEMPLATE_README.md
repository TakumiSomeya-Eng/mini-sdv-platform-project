# Interview Document Generator Template

英語でのインタビュー準備資料（docx）を自動生成するテンプレートです。

## 📋 テンプレート構成

```
1. Background & Challenge
   - Project Context
   - Problem Statement

2. Solution & Approach
   - Proposed Solution
   - Implementation Strategy

3. Implementation Details
   - Tech Stack (テーブル)
   - Key Features

4. Demonstration & Usage
   - Setup Instructions
   - Demo Workflow

5. FAQ & Technical Questions
   - よくある質問（自由に追加）

6. Future Improvements
   - 今後の改善案

Appendix: Code References
   - Repository Structure
   - Key Files
   - Related Documentation
```

## 🚀 使い方

### CLIコマンド

```bash
./generate_interview.sh FR001 "User Authentication System" "MyProject"
```

**出力例:**
```
FR001_interview_user_authentication_system.docx
```

### Node.js スクリプト直接実行

```bash
node generate_interview_doc.js FR001 "Feature Title"
```

### Claude Code CLI との組み合わせ

```bash
# 実装完了後
claude --enable-auto-mode

# プロンプト例:
# "Generate interview documentation: FR001, User Authentication System"
# → 自動的にdocxが生成される
```

## 📝 カスタマイズ

`generate_interview_doc.js` の `config` セクションを編集：

```javascript
const config = {
  frNumber: 'FR001',              // FR番号
  frTitle: 'Feature Title',        // 機能名
  projectName: 'Project Name',     // プロジェクト名
  techStack: ['Node.js', 'React'], // 技術スタック
  gitHubUrl: 'https://...',        // GitHub リポジトリURL
};
```

## 📂 ワークフロー（推奨）

```
FR001 実装完了 → GitHub push
         ↓
    Auto mode: "Generate interview doc"
         ↓
    FR001_interview_xxx.docx 生成
         ↓
    /docs/interview/ に保存
         ↓
    内容を編集・充実
         ↓
    GitHub push
```

## ✏️ 編集後のステップ

1. 生成されたdocxを開く
2. 各セクションのプレースホルダー `[  ]` を埋める
3. 実装詳細、FAQ、デモ手順を追加
4. `/docs/interview/` に保存
5. GitHub にコミット

```bash
git add docs/interview/FR001_interview_*.docx
git commit -m "docs: FR001 interview preparation material"
git push
```

## 🔧 スクリプト詳細

### generate_interview_doc.js
- Node.js の `docx` ライブラリを使用
- プロフェッショナルな Word フォーマット
- 見出し、テーブル、箇条書き対応
- A4 サイズ、1インチマージン設定済み

### 必須環境
- Node.js 14+
- `npm install -g docx`

## 📌 ポイント

✅ テンプレートは自由にカスタマイズ可能
✅ Auto mode で自動生成後、手動で詳細を追記
✅ GitHub で履歴管理（各FR単位）
✅ インタビュアー向けの説得力ある資料に仕上げる

---

**例：FR001 完成フロー**

```
1. Claude Code CLI で実装
2. GitHub に push
3. generate_interview.sh FR001 "Title" で docx 生成
4. 内容を充実させる
5. docs/interview/ に保存＆ push
```

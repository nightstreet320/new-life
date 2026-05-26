import json
import sqlite3
import re
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, jsonify
import requests
import os

app = Flask(__name__)

# ================== 配置 ==================
DEEPSEEK_API_KEY = "sk-2dc6d0d9316f414a9f17d111523f007a"  # 请替换
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# 初始化数据库
def init_db():
    conn = sqlite3.connect('ledger.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        category TEXT,
        amount REAL,
        payment TEXT,
        note TEXT,
        created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS sub_ledgers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS sub_ledger_items (
        sub_ledger_id INTEGER,
        transaction_id INTEGER,
        FOREIGN KEY(sub_ledger_id) REFERENCES sub_ledgers(id),
        FOREIGN KEY(transaction_id) REFERENCES transactions(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS rules (
        keyword TEXT PRIMARY KEY,
        target_field TEXT,
        target_value TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS budget (
        year_month TEXT PRIMARY KEY,
        total_budget REAL,
        expected_save REAL
    )''')
    conn.commit()
    conn.close()

def current_year_month():
    return datetime.now().strftime("%Y-%m")

def get_budget(year_month=None):
    if not year_month:
        year_month = current_year_month()
    conn = sqlite3.connect('ledger.db')
    c = conn.cursor()
    c.execute("SELECT total_budget, expected_save FROM budget WHERE year_month=?", (year_month,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"total_budget": row[0], "expected_save": row[1]}
    return None

def set_budget(year_month, total_budget, expected_save):
    conn = sqlite3.connect('ledger.db')
    c = conn.cursor()
    c.execute("REPLACE INTO budget (year_month, total_budget, expected_save) VALUES (?, ?, ?)",
              (year_month, total_budget, expected_save))
    conn.commit()
    conn.close()

EXPENSE_CATEGORIES = ["吃饭", "买菜", "交通", "衣物购入", "生活用品购入", "好玩的小玩意购入", "娱乐"]
INCOME_CATEGORIES = ["赚钱", "生活费入账"]

def get_month_expense(year_month=None):
    if not year_month:
        year_month = current_year_month()
    conn = sqlite3.connect('ledger.db')
    c = conn.cursor()
    placeholders = ','.join(['?' for _ in EXPENSE_CATEGORIES])
    c.execute(f"SELECT SUM(amount) FROM transactions WHERE date LIKE ? AND category IN ({placeholders})",
              (f"{year_month}%", *EXPENSE_CATEGORIES))
    total = c.fetchone()[0] or 0
    conn.close()
    return total

def add_rule(keyword, target_field, target_value):
    conn = sqlite3.connect('ledger.db')
    c = conn.cursor()
    c.execute("REPLACE INTO rules (keyword, target_field, target_value) VALUES (?, ?, ?)",
              (keyword, target_field, target_value))
    conn.commit()
    conn.close()

def parse_with_ai(user_text):
    conn = sqlite3.connect('ledger.db')
    c = conn.cursor()
    c.execute("SELECT keyword, target_value FROM rules WHERE target_field='category'")
    cat_rules = c.fetchall()
    c.execute("SELECT keyword, target_value FROM rules WHERE target_field='payment'")
    pay_rules = c.fetchall()
    conn.close()
    
    cat_hint = "；".join([f"如果包含'{kw}'，类别设为'{val}'" for kw, val in cat_rules]) if cat_rules else ""
    pay_hint = "；".join([f"如果包含'{kw}'，支付方式设为'{val}'" for kw, val in pay_rules]) if pay_rules else ""
    
    categories = "、".join(EXPENSE_CATEGORIES + INCOME_CATEGORIES)
    payments = "微信、支付宝、银行卡、朋友或家人送礼、其他"
    
    system_prompt = f"""你是一个记账助手。从用户的输入中提取：金额(数字)、类别(必须从以下中选择：{categories})、支付方式(从以下中选择：{payments})、备注(剩余的文字)。
如果用户提到相对日期如“昨天”、“前天”，也要提取日期，格式YYYY-MM-DD。如果没有提到日期，则日期字段为null。
输出必须是一个JSON对象，包含字段：amount, category, payment, note, date。
{cat_hint}
{pay_hint}
只输出JSON，不要有其他解释。"""
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text}
    ]
    
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.1
    }
    try:
        resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=data, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        content = result['choices'][0]['message']['content']
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = {}
        return parsed
    except Exception as e:
        print("AI解析失败:", e)
        return {"error": str(e)}

def save_transaction(date_str, category, amount, payment, note):
    conn = sqlite3.connect('ledger.db')
    c = conn.cursor()
    c.execute("INSERT INTO transactions (date, category, amount, payment, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
              (date_str, category, amount, payment, note, datetime.now().isoformat()))
    conn.commit()
    trans_id = c.lastrowid
    conn.close()
    return trans_id

def get_all_transactions(year_month=None):
    conn = sqlite3.connect('ledger.db')
    c = conn.cursor()
    if year_month:
        c.execute("SELECT id, date, category, amount, payment, note FROM transactions WHERE date LIKE ? ORDER BY date DESC", (f"{year_month}%",))
    else:
        c.execute("SELECT id, date, category, amount, payment, note FROM transactions ORDER BY date DESC")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "date": r[1], "category": r[2], "amount": r[3], "payment": r[4], "note": r[5]} for r in rows]

def generate_monthly_summary(year_month=None):
    if not year_month:
        year_month = current_year_month()
    total_expense = get_month_expense(year_month)
    total_income = 0
    conn = sqlite3.connect('ledger.db')
    c = conn.cursor()
    for cat in INCOME_CATEGORIES:
        c.execute("SELECT SUM(amount) FROM transactions WHERE date LIKE ? AND category=?", (f"{year_month}%", cat))
        inc = c.fetchone()[0] or 0
        total_income += inc
    conn.close()
    net = total_income - total_expense
    budget_info = get_budget(year_month)
    budget = budget_info["total_budget"] if budget_info else None
    expected_save = budget_info["expected_save"] if budget_info else None
    conn = sqlite3.connect('ledger.db')
    c = conn.cursor()
    placeholders = ','.join(['?' for _ in EXPENSE_CATEGORIES])
    c.execute(f"SELECT category, SUM(amount) FROM transactions WHERE date LIKE ? AND category IN ({placeholders}) GROUP BY category ORDER BY SUM(amount) DESC LIMIT 3",
              (f"{year_month}%", *EXPENSE_CATEGORIES))
    top3 = c.fetchall()
    conn.close()
    over_budget = None
    if budget is not None:
        if total_expense > budget:
            over_budget = f"超支 {total_expense - budget:.2f}"
        else:
            over_budget = f"剩余 {budget - total_expense:.2f}"
    save_achieve = None
    if expected_save is not None:
        actual_save = total_income - total_expense
        if actual_save >= expected_save:
            save_achieve = f"达成省钱目标！实际节省 {actual_save:.2f} (目标 {expected_save:.2f})"
        else:
            save_achieve = f"未达成省钱目标，差 {expected_save - actual_save:.2f}"
    return {
        "year_month": year_month,
        "total_expense": total_expense,
        "total_income": total_income,
        "net": net,
        "budget": budget,
        "expected_save": expected_save,
        "top3_expense": [{"category": c[0], "amount": c[1]} for c in top3],
        "over_budget": over_budget,
        "save_achieve": save_achieve
    }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    user_text = data.get('message', '')
    if user_text.startswith('/预算'):
        parts = user_text.split()
        if len(parts) >= 2:
            try:
                total = float(parts[1])
                expected = float(parts[2]) if len(parts) >= 3 else None
                ym = current_year_month()
                set_budget(ym, total, expected)
                return jsonify({"reply": f"已设置本月总预算 {total} 元" + (f"，期望节省 {expected} 元" if expected else "")})
            except:
                return jsonify({"reply": "格式错误，使用：/预算 金额 [期望节省金额]"})
    if user_text.startswith('/总结'):
        ym = current_year_month()
        summary = generate_monthly_summary(ym)
        reply = f"📊 {ym} 月度总结\n总支出: {summary['total_expense']:.2f}\n总收入: {summary['total_income']:.2f}\n结余: {summary['net']:.2f}\n"
        if summary['budget']:
            reply += f"预算: {summary['budget']:.2f}，{summary['over_budget']}\n"
        if summary['expected_save']:
            reply += f"{summary['save_achieve']}\n"
        reply += "支出TOP3:\n" + "\n".join([f"{t['category']}: {t['amount']:.2f}" for t in summary['top3_expense']])
        return jsonify({"reply": reply})
    if user_text.startswith('/规则'):
        parts = user_text.split()
        if len(parts) >= 4:
            keyword = parts[1]
            field = parts[2]
            value = parts[3]
            add_rule(keyword, field, value)
            return jsonify({"reply": f"已添加规则：当出现'{keyword}'，{field}设为'{value}'"})
        else:
            return jsonify({"reply": "格式：/规则 关键词 category(或payment) 值"})
    parsed = parse_with_ai(user_text)
    if "error" in parsed:
        return jsonify({"reply": f"解析失败：{parsed['error']}，请重新描述。示例：外卖38元"})
    amount = parsed.get("amount")
    if not amount:
        return jsonify({"reply": "未识别到金额，请包含数字。例如：打车25元"})
    category = parsed.get("category")
    if not category or category not in (EXPENSE_CATEGORIES + INCOME_CATEGORIES):
        return jsonify({"reply": f"未能识别类别，请从以下选择：{EXPENSE_CATEGORIES+INCOME_CATEGORIES}"})
    payment = parsed.get("payment", "其他")
    if payment not in ["微信", "支付宝", "银行卡", "朋友或家人送礼", "其他"]:
        payment = "其他"
    note = parsed.get("note", "")
    date_str = parsed.get("date")
    if not date_str:
        date_str = date.today().isoformat()
    else:
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except:
            date_str = date.today().isoformat()
    trans_id = save_transaction(date_str, category, amount, payment, note)
    ym = current_year_month()
    total_expense = get_month_expense(ym)
    budget_info = get_budget(ym)
    budget_str = ""
    if budget_info and budget_info["total_budget"]:
        used = total_expense
        total_budget = budget_info["total_budget"]
        remaining = total_budget - used
        budget_str = f"本月已支出 {used:.2f} / 预算 {total_budget:.2f}，剩余 {remaining:.2f}"
    reply = f"✅ 已记录：{category} {amount}元 ({payment})，备注：{note}，日期：{date_str}\n{budget_str}"
    return jsonify({"reply": reply, "transaction": {"id": trans_id, "date": date_str, "category": category, "amount": amount, "payment": payment, "note": note}})

@app.route('/api/transactions', methods=['GET'])
def get_transactions():
    ym = request.args.get('month')
    return jsonify(get_all_transactions(ym))

@app.route('/api/summary', methods=['GET'])
def summary():
    ym = request.args.get('month')
    if not ym:
        ym = current_year_month()
    return jsonify(generate_monthly_summary(ym))

@app.route('/api/budget', methods=['GET'])
def get_budget_api():
    ym = current_year_month()
    b = get_budget(ym)
    if b:
        return jsonify({"total_budget": b["total_budget"], "expected_save": b["expected_save"]})
    return jsonify({"total_budget": None, "expected_save": None})

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
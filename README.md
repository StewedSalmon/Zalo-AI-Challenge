This is the spec for DataMate, a Zalo AI Challenge 2026 task. You build an AI agent that answers Vietnamese natural-language questions about small-business spreadsheets and databases, and returns a correct answer or declines when the data can’t support the question.

The task

Input: a business’s data files (CSV, Excel, or SQLite) with a short README, a Vietnamese question such as “tháng này hàng nào bán chậm nhất?”, and an as-of date that anchors relative terms like “tháng này” (this month) and “tuần rồi” (last week).
Output: one answer per question, of one of these types: number, percent, string, date, boolean, list, table, unanswerable (the data can’t support the question), or abstain (the agent chooses not to answer).

Rules and data

Each split has its own rulebook.md that sets default meanings. Examples: revenue excludes VAT, delivery fees, and cancelled or returned orders; “bán chạy” means best-selling by quantity; weeks run Monday to Sunday; the Tết window runs from 23 Chạp to mùng 10 on the lunar calendar. Your agent must read the rulebook for the split it’s answering.
Business types include grocery stores, cafés, pharmacies, and repair shops. The private test set includes business types absent from training. Some READMEs are thorough and others barely exist.
Training data includes answers.jsonl with reference answers and accepted aliases. Test data doesn’t.

Scoring

Each answer is correct, wrong, or abstain. Type and value must both match. There’s no partial credit.
Rankings use the criterion in the question or the rulebook default. Questions with ties at the boundary are excluded.
The leaderboard metric is Penalized Accuracy. Answering an unanswerable question counts as wrong. 
Ties are broken by total inference time in the organizers’ re-run, then by submission time.

Submission

A submission.jsonl file, one JSON object per line. Missing questions count as abstain.
A Docker image containing your code, weights, and a predict.sh that reads /data and writes /result/submission.jsonl, plus a per-question log in /result/logs/ (question ID, final answer, elapsed time, and how the answer was reached). The organizers use these logs to resolve discrepancies when they re-run your system.

Sample record in questions.jsonl:

{"id": "q_01234", "business_id": "biz_017", "question": "thang nay hang nao ban cham nhat, ton nhieu ma ban ko dc", "as_of_date": "2026-08-27"}

Sample record in answers.jsonl (the reference answer of the example question in the Description  section):

{"id": "q_06de33fa21cc", "type": "string", "answer": "Xi măng Hà Tiên PCB40 bao 50kg", "alias": ["SP0001"]}
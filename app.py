import io
import json
import os
from datetime import datetime

from Bio.Blast import NCBIWWW, NCBIXML
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.secret_key = os.urandom(24)  # 用于会话管理
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'  # 设置登录重定向视图


# 用户模型
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)


# 任务模型
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    taskname = db.Column(db.String(120), unique=True, nullable=False)
    program = db.Column(db.String(80), nullable=False)
    database = db.Column(db.String(80), nullable=False)
    sequence = db.Column(db.Text, nullable=False)
    result = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user = db.relationship('User', backref=db.backref('tasks', lazy=True))


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def parse_blast_results(blast_results):
    """解析 BLAST XML 结果并返回结构化摘要。"""
    blast_records = NCBIXML.parse(blast_results)
    parsed_results = []

    for blast_record in blast_records:
        for alignment in blast_record.alignments:
            for hsp in alignment.hsps:
                parsed_results.append({
                    "title": alignment.title,
                    "length": alignment.length,
                    "score": hsp.score,
                    "e_value": hsp.expect,
                    "query_start": hsp.query_start,
                    "query_end": hsp.query_end,
                    "qseq": hsp.query,
                    "match": hsp.match,
                    "hseq": hsp.sbjct
                })

    return parsed_results


@app.route('/')
def main_index():
    return render_template('main_index.html')


@app.route('/<username>')
@login_required
def user_index(username):
    # 检查请求的用户名是否与当前用户的用户名匹配
    if username != current_user.username:
        flash('无权访问该用户的主页！', 'danger')
        return redirect(url_for('user_index', username=current_user.username))

    user = User.query.filter_by(username=username).first_or_404()
    tasks = Task.query.filter_by(user_id=user.id).all()
    return render_template('user_index.html', tasks=tasks, user=user)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            return render_template('register.html', username_taken=True)  # 设置用户名已被注册标志
        new_user = User(username=username, password=password)
        db.session.add(new_user)
        db.session.commit()
        flash('注册成功，请登录！', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()

        if user and user.password == password:
            login_user(user)
            return redirect(url_for('user_index', username=user.username))
        else:
            return render_template('login.html', error=True)  # 设置错误标志

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main_index'))


@app.route('/blast', methods=['GET', 'POST'])
@login_required
def blast():
    task_running = False
    if request.method == 'POST':
        task_running = True  # 设置任务正在运行的标志
        data = request.form
        program = data.get('program')
        database = data.get('database')
        sequence = data.get('sequence')

        try:
            # 向 NCBI 发送 BLAST 查询
            result_handle = NCBIWWW.qblast(program, database, sequence)
            blast_results = result_handle.read()
            result_handle.close()

            # 解析 BLAST 结果
            parsed_results = parse_blast_results(io.StringIO(blast_results))

            # 生成格式化的任务名称
            formatted_taskname = f"{program}_{database}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            # 将结果保存到数据库
            task = Task(
                taskname=formatted_taskname,
                program=program,
                database=database,
                sequence=sequence,
                result=json.dumps(parsed_results),
                user_id=current_user.id
            )
            db.session.add(task)
            db.session.commit()

            flash('BLAST成功!', 'success')
            return redirect(url_for('user_index', username=current_user.username))
        except Exception as e:
            flash(f"发生错误: {str(e)}", 'danger')
            return render_template('blast.html', task_running=task_running)

    return render_template('blast.html', task_running=task_running)


@app.route('/<username>/<taskname>', methods=['GET'])
@login_required
def results(username, taskname):
    user = User.query.filter_by(username=username).first_or_404()
    task = Task.query.filter_by(taskname=taskname, user_id=user.id).first_or_404()

    # 检查任务的拥有者是否是当前用户
    if task.user_id != current_user.id:
        flash('无权访问该任务的结果！', 'danger')
        return redirect(url_for('user_index', username=current_user.username))

    # 解析结果
    parsed_results = json.loads(task.result) if task.result else []
    return render_template('results.html', task=task, results=parsed_results)


@app.route('/delete_task/<int:task_id>', methods=['POST'])
@login_required
def delete_task(task_id):
    task = Task.query.get(task_id)
    if task and task.user_id == current_user.id:
        db.session.delete(task)
        db.session.commit()
        flash('任务已成功删除！', 'success')
    else:
        flash('无权删除该任务或任务不存在！', 'error')
    return redirect(url_for('user_index', username=current_user.username))


@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404


@app.errorhandler(Exception)
def handle_exception(error):
    response = jsonify({"error": str(error)})
    response.status_code = 500
    return response


if __name__ == '__main__':
    with app.app_context():
        db.create_all()  # 创建数据库表
    app.run(debug=True)

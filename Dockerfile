# 必须钉 3.11：pgserver==0.1.4 在 PyPI 上没有 Python 3.13 的发行版（实测
# `pip download pgserver==0.1.4 --python-version 3.13` 报 no matching distribution）。
FROM python:3.11-slim

# 不用 root 跑：容器逃逸时少一层损失
RUN groupadd --system app && useradd --system --gid app --home /srv/app app

WORKDIR /srv/app

# 先只拷 requirements 再装依赖：改代码不会让依赖层缓存失效
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 本地存储后端的目录（STORAGE_LOCAL_ROOT），要可写
RUN mkdir -p storage && chown -R app:app /srv/app
USER app

EXPOSE 8000

# 容器里必须监听 0.0.0.0，绑 127.0.0.1 的话宿主机连不进来
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

# 必须钉 3.11：pgserver==0.1.4 在 PyPI 上没有 Python 3.13 的发行版（实测
# `pip download pgserver==0.1.4 --python-version 3.13` 报 no matching distribution）。
FROM python:3.11-slim

# 不用 root 跑：容器逃逸时少一层损失
RUN groupadd --system app && useradd --system --gid app --home /srv/app app

WORKDIR /srv/app

# 先只拷 requirements 再装依赖：改代码不会让依赖层缓存失效
COPY requirements.txt .
# ⚠️ 这里会连测试与 lint 依赖一起装进生产镜像，多占约 60 MB
#    （实测 pgserver 33 MB、ruff 23.3 MB、pytest 2.6 MB）。
#    **这是有意的，不是疏漏 —— 见 TD-202。** 本仓库刻意只有 requirements.txt 一个清单
#    （总览.md §6.1 把「requirements-dev.txt 不存在 —— 一条命令装齐」写成设计事实），
#    因为拆成两个文件要同步改 14 个文件里 45 处引用，而文档同步恰是本仓库反复失守的环节。
#    这 60 MB 只影响一次拉取，不影响启动时间与内存占用。
#    若将来镜像体积成为实际约束，再拆；拆时必须同时更新那 45 处。
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 本地存储后端的目录（STORAGE_LOCAL_ROOT），要可写
RUN mkdir -p storage && chown -R app:app /srv/app
USER app

EXPOSE 8000

# 自带健康检查：没有它，编排器只能靠「进程还在不在」判断健康 ——
# 而进程活着但事件循环被堵死、或连接池耗尽时，进程照样在，
# 于是坏副本一直留在负载均衡里接流量，滚动发布也不会被判定失败。
#
# ⚠️ 用 python 发请求而不是 curl/wget：**python:3.11-slim 两者都没有**，
#    写 curl 会让每一次健康检查都失败，好副本反而被反复重启。
# 打 /healthz（存活探针）而不是 /readyz：readyz 会真连一次数据库，
#    数据库抖一下就会把**所有**副本同时判死、一起重启，是典型的雪崩放大器。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"


# 容器里必须监听 0.0.0.0，绑 127.0.0.1 的话宿主机连不进来
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

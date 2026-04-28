# docker build -t qwerty/oas:latest .
# docker run -v ${PWD}:/app/OnmyojiAutoScript -p 22288:22288 --name oas -it --rm qwerty/oas


FROM python:3.10-slim-bookworm

WORKDIR /app/OnmyojiAutoScript

COPY requirements.txt /tmp/requirements.txt

# python:3.10-slim is based on debian:12, apt source from https://developer.aliyun.com/mirror/debian
RUN echo "\
deb https://mirrors.cloud.tencent.com/debian/ bookworm main non-free non-free-firmware contrib \n\
deb-src https://mirrors.cloud.tencent.com/debian/ bookworm main non-free non-free-firmware contrib \n\
deb https://mirrors.cloud.tencent.com/debian-security/ bookworm-security main \n\
deb-src https://mirrors.cloud.tencent.com/debian-security/ bookworm-security main \n\
deb https://mirrors.cloud.tencent.com/debian/ bookworm-updates main non-free non-free-firmware contrib \n\
deb-src https://mirrors.cloud.tencent.com/debian/ bookworm-updates main non-free non-free-firmware contrib \n\
deb https://mirrors.cloud.tencent.com/debian/ bookworm-backports main non-free non-free-firmware contrib \n\
deb-src https://mirrors.cloud.tencent.com/debian/ bookworm-backports main non-free non-free-firmware contrib \
    "\
> /etc/apt/sources.list\
 && apt update \
 && apt install -y git adb libgomp1 openssh-client \
 && (apt install -y prelink || true) \
 && git config --global --add safe.directory '*' \
 && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
 && echo 'Asia/Shanghai' > /etc/timezone \
 && pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
 && grep -v "pywin32" /tmp/requirements.txt | sed 's/lxml==5.0.0/lxml==4.9.3/' > /tmp/requirements_fixed.txt \
 && pip install -r /tmp/requirements_fixed.txt \
 && pip uninstall -y opencv-python\
 && pip uninstall -y opencv-python-headless\
 && pip install opencv-python-headless==4.7.0.72\
 && (command -v execstack >/dev/null && execstack -c /usr/local/lib/python3.10/site-packages/onnxruntime/capi/*.so 2>/dev/null || true) \
 && rm -f /tmp/requirements.txt /tmp/requirements_fixed.txt \
 && rm -rf ~/.cache/pip

CMD sh -c "\
    cd /app/OnmyojiAutoScript && \
    if command -v git >/dev/null 2>&1 && [ -f config/deploy.yaml ]; then \
        AUTO_UPDATE=\$(grep 'AutoUpdate:' config/deploy.yaml | awk '{print \$2}'); \
        TARGET_BRANCH=\$(grep 'Branch:' config/deploy.yaml | awk '{print \$2}'); \
        if [ \"\$AUTO_UPDATE\" = \"true\" ] && [ -d .git ]; then \
            echo \"Checking for updates on branch \$TARGET_BRANCH...\"; \
            git fetch origin && \
            CURRENT_BRANCH=\$(git branch --show-current) && \
            if [ \"\$CURRENT_BRANCH\" != \"\$TARGET_BRANCH\" ]; then \
                echo \"Switching to branch \$TARGET_BRANCH...\"; \
                git checkout \$TARGET_BRANCH 2>/dev/null || git checkout -b \$TARGET_BRANCH origin/\$TARGET_BRANCH; \
            fi && \
            LOCAL=\$(git rev-parse HEAD) && \
            REMOTE=\$(git rev-parse origin/\$TARGET_BRANCH 2>/dev/null || echo \$LOCAL) && \
            if [ \"\$LOCAL\" != \"\$REMOTE\" ]; then \
                echo \"Updating to latest version...\"; \
                git reset --hard origin/\$TARGET_BRANCH; \
            else \
                echo \"Already up to date.\"; \
            fi; \
        fi; \
    fi && \
    python server.py"
# TestAgent Cloud 沙盒服务

## 前置需求

1. 后台启动一个 OpenSandbox 服务，并不额外设置 API Key
2. 后台启动一个注册表 (容器仓库) 服务，可以参考下方的 compose 文件

```yaml
services:
  registry:
    image: registry:3
    container_name: registry-test
    restart: unless-stopped
    ports:
      - "5000:5000"

  registry-ui:
    image: joxit/docker-registry-ui:latest
    container_name: registry-test-ui
    restart: unless-stopped
    ports:
      - "5050:80"
    environment:
      - SINGLE_REGISTRY=true
      - REGISTRY_TITLE=Local Docker Registry
      - NGINX_PROXY_PASS_URL=http://registry-test:5000
      - DELETE_IMAGES=true
      - SHOW_CONTENT_DIGEST=true
```

3. 本地准备一个可以用于测试的 docker 镜像

## 编译与使用

复制 `.env.example` 且重命名为 `.env`，
并根据需要修改配置

在项目根目录执行 `docker build -t testagent/testagent-cloud-sandbox-service:latest .` 构建镜像

在项目根目录执行 `docker compose up -d`

如果没有修改默认的映射端口，则访问 `http://localhost:8080/docs` 查看 API 文档，
鉴权密码在 `.env` 中设置。

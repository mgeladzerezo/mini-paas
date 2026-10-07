FROM node:22-alpine AS build

WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build


FROM nginxinc/nginx-unprivileged:1.27-alpine

# The entrypoint renders templates with envsubst, limited to the variables named here
# so nginx's own $variables are left alone.
ENV API_UPSTREAM=http://api:8000 \
    NGINX_ENVSUBST_FILTER=API_UPSTREAM
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /src/dist /usr/share/nginx/html

EXPOSE 8080

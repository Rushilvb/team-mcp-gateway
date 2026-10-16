# build: install exact deps from package-lock.json, type-check, bundle with Vite.
FROM node:24.21.0-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run typecheck && npm run build

# test: `make test` runs the type-checker (it already passed in the build stage).
FROM build AS test
CMD ["npm", "run", "typecheck"]

# runtime: just nginx (non-root, port 8080) and the static files. No Node in production.
FROM nginxinc/nginx-unprivileged:1.30.5-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080

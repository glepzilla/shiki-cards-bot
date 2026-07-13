FROM node:22-bookworm-slim
WORKDIR /app
COPY renderer/package.json ./package.json
RUN npm install --omit=dev --no-audit --no-fund
COPY renderer/server.js ./server.js
COPY bot/app/static/card-renderer.js ./card-renderer.js
USER node
EXPOSE 3000
CMD ["node", "server.js"]

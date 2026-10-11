FROM node:20-alpine

WORKDIR /app

# Run the CRA dev server with hot reload for local development/testing.
# There is no production build/serving story yet (see README technical
# debt notes), so this intentionally mirrors `npm start`, not a static
# nginx build.
ENV CHOKIDAR_USEPOLLING=true \
    WATCHPACK_POLLING=true \
    BROWSER=none

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 3000

CMD ["npm", "start"]

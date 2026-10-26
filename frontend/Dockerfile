FROM node:22-alpine

WORKDIR /app

# Run the Vite dev server with hot reload for local development (polling is set in vite.config.ts).

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 3000

CMD ["npm", "start"]

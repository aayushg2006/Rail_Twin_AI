FROM node:24-bookworm-slim

WORKDIR /app

# Install dependencies while building the image. The Compose node_modules
# volume is initialized from this directory, so startup does not need to run
# npm install before Vite can start.
COPY package.json bun.lock ./
RUN npm install --no-audit --no-fund

EXPOSE 8080

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "8080"]

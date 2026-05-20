FROM node:20-alpine AS build
WORKDIR /app
COPY widget/package.json widget/package-lock.json* ./
RUN npm install
COPY widget/ ./
RUN npm run build

FROM nginx:alpine
# `widget` nginx serves the Vite-built Preact bundle as /widget-bundle.js.
# The public-facing /widget.js loader is served by the FastAPI api container
# (see app/api/loader.py).
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

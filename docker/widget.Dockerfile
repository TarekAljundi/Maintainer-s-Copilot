FROM node:20-alpine AS build
WORKDIR /app
COPY widget/package.json widget/package-lock.json* ./
RUN npm install
COPY widget/ ./
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY widget/public/loader.js /usr/share/nginx/html/widget.js
EXPOSE 80

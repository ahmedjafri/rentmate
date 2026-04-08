# Frontend Dockerfile
FROM node:20-slim

# System dependencies
RUN apt-get update && apt-get install -y curl git && rm -rf /var/lib/apt/lists/*

WORKDIR /app/www/rentmate-ui

# Copy frontend package files
COPY www/rentmate-ui/package*.json ./

# Install frontend dependencies
RUN npm install

# Copy the rest of the frontend code
COPY www/rentmate-ui/ ./

# Expose Vite dev server port
EXPOSE 8080

# Run Vite in development mode
# Note: Host '::' allows access from outside the container
CMD ["npm", "run", "dev:fe", "--", "--host", "0.0.0.0"]

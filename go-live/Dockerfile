FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN go test ./... && CGO_ENABLED=0 go build -mod=readonly -trimpath -ldflags='-s -w' -o /live .

FROM alpine:3.20
RUN adduser -D -H live
USER live
COPY --from=build /live /live
EXPOSE 8080
ENTRYPOINT ["/live"]

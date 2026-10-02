FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src
# Restore first so the package layer is cached across source-only changes.
COPY src/CompanyAgent.Api/CompanyAgent.Api.csproj src/CompanyAgent.Api/
RUN dotnet restore src/CompanyAgent.Api/CompanyAgent.Api.csproj
COPY src/ src/
RUN dotnet publish src/CompanyAgent.Api/CompanyAgent.Api.csproj -c Release -o /app/publish --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:8.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /app/publish .
COPY skills/ /app/skills/
# The base image defaults to port 8080; the app binds SERVER_PORT itself, so clear the default.
ENV ASPNETCORE_HTTP_PORTS= \
    SERVER_PORT=8090 \
    COMPANYAGENT_SKILLS_DIR=/app/skills \
    COMPANYAGENT_DATA_DIR=/app/data/dotnet \
    DOTNET_gcServer=0
RUN mkdir -p /app/data && chown -R app:app /app/data
USER app
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:8090/health || exit 1
ENTRYPOINT ["dotnet", "CompanyAgent.Api.dll"]

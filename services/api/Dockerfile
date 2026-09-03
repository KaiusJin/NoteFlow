# syntax=docker/dockerfile:1.7

FROM eclipse-temurin:21-jdk-noble AS build
WORKDIR /workspace

COPY gradlew settings.gradle build.gradle gradle.lockfile ./
COPY gradle ./gradle
RUN chmod +x gradlew

# Keep dependency resolution in its own cached layer. The final build runs
# offline so an untracked dependency cannot appear between the two steps.
RUN ./gradlew dependencies --no-daemon
COPY src ./src
RUN ./gradlew bootJar --no-daemon \
    && cp build/libs/noteflow-api-*.jar /workspace/noteflow-api.jar

FROM eclipse-temurin:21-jre-noble AS runtime
RUN groupadd --system --gid 10001 noteflow \
    && useradd --system --uid 10001 --gid noteflow --home-dir /nonexistent --shell /usr/sbin/nologin noteflow
WORKDIR /app
COPY --from=build --chown=noteflow:noteflow /workspace/noteflow-api.jar /app/noteflow-api.jar

USER 10001:10001
ENV JAVA_TOOL_OPTIONS="-XX:+UseContainerSupport -XX:MaxRAMPercentage=75.0" \
    SERVER_PORT=8080
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/noteflow-api.jar"]

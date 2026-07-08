# check=skip=SecretsUsedInArgOrEnv
FROM toolbox:latest

# label-studio
ENV LATEST_VERSION_CHECK=0 \
    COLLECT_ANALYTICS=false \
    SENTRY_DSN= \
    FRONTEND_SENTRY_DSN= \
    SENTRY_RATE=0 \
    fflag_feat_front_lsdv_e_297_increase_oss_to_enterprise_adoption_short=false \
    LOCAL_FILES_DOCUMENT_ROOT=/data \
    LOCAL_FILES_SERVING_ENABLED=true \
    LABEL_STUDIO_PASSWORD=hackme \
    LABEL_STUDIO_USERNAME=user@localhost \
    LABEL_STUDIO_USER_TOKEN=hackme123 \
    LABEL_STUDIO_API_KEY=hackme123 \
    LABEL_STUDIO_ENABLE_LEGACY_API_TOKEN=true \
    VERIFY_SSL=false \
    LABEL_STUDIO_BASE_DATA_DIR=/persist/label-studio

# mlflow
ENV MLFLOW_DISABLE_TELEMETRY="true" \
    DO_NOT_TRACK="true" \
    MLFLOW_BACKEND_STORE_URI=sqlite:////persist/mlflow/mlflow.db \
    MLFLOW_ARTIFACTS_DESTINATION=/persist/mlflow/artifacts \
    TOOLBOX_DATA=/persist/toolbox

ARG TOOLBOX_BRAND_NAME_LONG
ARG TOOLBOX_BRAND_NAME_SHORT
ENV TOOLBOX_BRAND_NAME_LONG=$TOOLBOX_BRAND_NAME_LONG \
    TOOLBOX_BRAND_NAME_SHORT=$TOOLBOX_BRAND_NAME_SHORT

COPY --from=branding properties.css /opt/apps/nexus/static/properties.css

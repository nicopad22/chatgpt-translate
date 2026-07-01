gcloud run deploy translate-api \
  --project project-automation-498114 \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --no-cpu-throttling \
  --min-instances 0 \
  --max-instances 1 \
  --timeout 3600 \
  --memory 512Mi \
  --set-env-vars "OPENAI_API_KEY=$OPENAI_API_KEY,
APP_PASSWORD=$APP_PASSWORD,
APP_SECRET=$APP_SECRET,
GCS_BUCKET=$GCS_BUCKET,
CORS_ORIGIN=$CORS_ORIGIN" &

cd frontend/
vercel --prod
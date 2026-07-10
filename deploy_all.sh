if [ -f .env ]; then
  export $(echo $(grep -v '^#' .env | xargs) | envsubst)
fi 

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
GCS_BUCKET=$GCS_BUCKET,
CORS_ORIGIN=$CORS_ORIGIN,
SUPABASE_URL=$SUPABASE_URL,
SUPABASE_JWT_SECRET=$SUPABASE_JWT_SECRET,
SUPABASE_SERVICE_ROLE_KEY=$SUPABASE_SERVICE_ROLE_KEY"

cd frontend/
vercel --prod
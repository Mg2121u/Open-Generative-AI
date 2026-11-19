# SaladCloud organization connection

The SaladCloud organization slug is mgg1981; the intended project slug is aria-nicole-studio. SALAD_API_KEY is a GitHub Actions secret. Never copy it into Git history, public Vercel variables, or logs.

The draft branch includes a read-only verification workflow:
- It runs automatically when pushed to the studio feature branch.
- It calls GET /api/public/organizations/mgg1981/quotas to check authentication.
- It lists container groups under project aria-nicole-studio (GET).
- It never creates a Salad resource, starts an instance, or spends the prepaid credit.
- It logs whether organization authentication succeeded and whether the chosen project exists.

If the project probe returns HTTP 404, create the project in the Salad portal. A GitHub secret does not give ChatGPT direct SaladCloud account access; only GitHub Actions can use this credential.

After connection is confirmed: publish the Docker image, configure a private S3-compatible bucket, then create a stopped container group. Start GPU compute only once image/storage configuration and spend limits are reviewed.

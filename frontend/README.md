# AlphaStock frontend

The React product interface is part of the AlphaStock monorepo.

```bash
cd frontend/react-app
npm ci
npm run dev
```

`npm run build` writes the production site to `frontend/react-app/dist/`.
The directory is generated and intentionally excluded from Git.

GitHub Actions validates the production build on pull requests and, after a
successful push to `main`, deploys it to the existing Nginx document root on
the AlphaStock server. The former `Alpha_stock_frontend` repository remains a
readable historical source during the transition; new product work belongs in
this directory.

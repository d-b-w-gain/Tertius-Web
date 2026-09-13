# BlueScope API workbench

Local Kubernetes UI for assessing and onboarding the BlueScope Building Components
purchase-order API against the existing shed procurement BoM.

The UI intentionally does not submit orders. The developer portal currently exposes
purchase-order APIs, not catalogue, stock, lead-time, or quote APIs.

## Deploy

```powershell
kubectl apply -k infra/local/bluescope-workbench
kubectl -n procurement-workbench rollout status deployment/bluescope-api-workbench
```

Open `http://<k3s-node-ip>:31823` (currently `http://192.168.88.29:31823`).

## Credentials

After BlueScope has approved a `BBC Purchase Orders` product subscription and issued
OAuth organisation credentials, create a Kubernetes Secret named
`bluescope-api-credentials` in the `procurement-workbench` namespace with these keys:

- `BLUESCOPE_SUBSCRIPTION_KEY`
- `BLUESCOPE_CLIENT_ID`
- `BLUESCOPE_CLIENT_SECRET`
- `BLUESCOPE_RESOURCE`

Never commit these values. Restart the deployment after creating or updating the
Secret. The UI reports only whether each value is present; it never returns values.

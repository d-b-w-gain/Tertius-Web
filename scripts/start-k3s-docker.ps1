param(
    [string]$ContainerName = "tertius-k3s",
    [string]$K3sVersion = "v1.34.1-k3s1",
    [string]$KubeconfigPath = "$PWD\.kube\tertius-k3s.yaml",
    [string]$Namespace = "tertius",
    [switch]$InstallOperators,
    [switch]$Reset
)

$ErrorActionPreference = "Stop"

function Require-Command($Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing required command '$Name'. Install it and rerun this script."
    }
}

function Require-DockerDaemon() {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    docker info *> $null
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    if ($exitCode -ne 0) {
        throw "Docker is installed but the Docker Desktop Linux engine is not reachable. Start Docker Desktop, wait until it reports running, then rerun this script."
    }
}

function Wait-ForK3s($Name) {
    $deadline = (Get-Date).AddMinutes(3)
    do {
        try {
            docker exec $Name kubectl get nodes | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return
            }
        } catch {
        }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)

    docker logs --tail 120 $Name
    throw "Timed out waiting for k3s to become ready in container '$Name'."
}

Require-Command docker
Require-Command kubectl
Require-DockerDaemon

if ($InstallOperators) {
    Require-Command helm
}

$existing = docker ps -a --filter "name=^/$ContainerName$" --format "{{.Names}}"
if ($existing -and $Reset) {
    docker rm -f $ContainerName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to remove existing Docker container '$ContainerName'."
    }
    $existing = ""
}

if (-not $existing) {
    docker run `
        --privileged `
        --name $ContainerName `
        --hostname $ContainerName `
        -p 6443:6443 `
        -d "rancher/k3s:$K3sVersion" `
        server `
        --write-kubeconfig-mode=644 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to start Docker container '$ContainerName' from rancher/k3s:$K3sVersion."
    }
} else {
    $running = docker ps --filter "name=^/$ContainerName$" --format "{{.Names}}"
    if (-not $running) {
        docker start $ContainerName | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to start existing Docker container '$ContainerName'."
        }
    }
}

Wait-ForK3s $ContainerName

$kubeconfigDir = Split-Path -Parent $KubeconfigPath
if ($kubeconfigDir) {
    New-Item -ItemType Directory -Force -Path $kubeconfigDir | Out-Null
}

docker cp "${ContainerName}:/etc/rancher/k3s/k3s.yaml" $KubeconfigPath
(Get-Content $KubeconfigPath) -replace "https://127.0.0.1:6443", "https://127.0.0.1:6443" |
    Set-Content -Path $KubeconfigPath -Encoding utf8

$env:KUBECONFIG = (Resolve-Path $KubeconfigPath).Path
kubectl config use-context default | Out-Null
kubectl wait --for=condition=Ready node --all --timeout=90s

if ($InstallOperators) {
    helm repo add cloudnativepg https://cloudnative-pg.github.io/charts | Out-Null
    helm repo update | Out-Null
    helm upgrade --install cloudnativepg cloudnativepg/cloudnative-pg `
        --namespace cnpg-system `
        --create-namespace `
        --wait `
        --timeout 5m

    kubectl apply -f https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/26.6.3/kubernetes/keycloaks.k8s.keycloak.org-v1.yml
    kubectl apply -f https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/26.6.3/kubernetes/keycloakrealmimports.k8s.keycloak.org-v1.yml
    kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n $Namespace apply -f https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/26.6.3/kubernetes/kubernetes.yml
    kubectl patch clusterrolebinding keycloak-operator-clusterrole-binding --type='json' -p="[{`"op`":`"replace`",`"path`":`"/subjects/0/namespace`",`"value`":`"$Namespace`"}]"
    kubectl -n $Namespace wait deploy keycloak-operator --for=condition=Available --timeout=90s
}

Write-Host ""
Write-Host "k3s debug cluster is ready."
Write-Host "KUBECONFIG=$env:KUBECONFIG"
Write-Host "K3S_CONTAINER=$ContainerName"
Write-Host ""
Write-Host "From Git Bash or WSL, run:"
Write-Host "  export KUBECONFIG='$($env:KUBECONFIG -replace '\\','/')'"
Write-Host "  export K3S_CONTAINER='$ContainerName'"
Write-Host "  scripts/test-k3s-deployment.sh"

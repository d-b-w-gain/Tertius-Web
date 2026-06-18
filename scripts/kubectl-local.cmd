@echo off
setlocal
set "ROOT=%~dp0.."
C:\tmp\kubectl\kubectl.exe --kubeconfig "%ROOT%\.kubectl\k3s.yaml" %*

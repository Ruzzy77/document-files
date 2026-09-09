# Candidate preparation only. Neither matching terms nor a successful build grants rights.
param(
    [Parameter(Mandatory=$true)][ValidateSet('cpu', 'recognition')][string]$Kind,
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Evidence,
    [Parameter(Mandatory=$true)][string]$Work,
    [Parameter(Mandatory=$true)][string]$Output,
    [Parameter(Mandatory=$true)][string]$TermsUrl,
    [Parameter(Mandatory=$true)][string]$TermsSha256,
    [string]$PackVersion = '',
    [string]$Cache = '',
    [ValidateRange(1,7200)][int]$BuildTimeoutSeconds = 2400
)
$ErrorActionPreference = 'Stop'
# Evidence is separate from builder work, which must remain nonexistent at launch.
New-Item -ItemType Directory -Path $Evidence -ErrorAction Stop | Out-Null
$receiptPath = Join-Path $Evidence 'windows-build-host.json'
$receipt = [ordered]@{
    schemaVersion='document-files.windows-build-host.v2'; status='started'; stage='preflight'
    executionRunId=[Guid]::NewGuid().ToString('D'); buildTimeoutSeconds=$BuildTimeoutSeconds
    startedAt=[DateTime]::UtcNow.ToString('o'); endedAt=$null; buildKind=$Kind
    sourceCommit=$env:GITHUB_SHA; independentRedistributionReview='pending'
    installedLicenseEntitlement='not-assessed'; staticCrtAndSdkReview='not-assessed'
    releaseQualification=$false; buildExitCode=$null
    requestedTerms=@{url=$TermsUrl; sha256=$TermsSha256}
}
function Save-Receipt {
    $pending = "$receiptPath.pending"
    $receipt | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $pending -Encoding utf8
    Move-Item -LiteralPath $pending -Destination $receiptPath -Force
}
Save-Receipt
try {
    if (git status --porcelain) { throw 'Candidate requires clean source' }
    if ($LASTEXITCODE -ne 0) { throw 'Source identity unavailable' }
    $receipt.sourceCommit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or ($env:GITHUB_SHA -and $receipt.sourceCommit -ne $env:GITHUB_SHA)) { throw 'Source commit mismatch' }
    $receipt.stage = 'installed-product'
    Save-Receipt
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) { throw 'Installed vswhere missing' }
    $found = & $vswhere -latest -products Microsoft.VisualStudio.Product.Enterprise -version '[17.0,18.0)' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -format json -utf8
    if ($LASTEXITCODE -ne 0) { throw 'vswhere failed' }
    $instances = @($found | ConvertFrom-Json)
    if ($instances.Count -ne 1) { throw 'One installed VS2022 Enterprise required' }
    $instance = $instances[0]
    $hostIdentity = [ordered]@{
        productId=$instance.productId; installationVersion=$instance.installationVersion
        installationPath=$instance.installationPath; isPrerelease=$instance.isPrerelease
        isComplete=$instance.isComplete; isLaunchable=$instance.isLaunchable
    }
    $hostPath = Join-Path $Evidence 'installed-product.json'
    $hostIdentity | ConvertTo-Json | Set-Content -LiteralPath $hostPath -Encoding utf8
    $receipt.installedProduct = $hostIdentity
    $receipt.stage = 'official-terms'
    Save-Receipt
    & $Python scripts/windows_build_evidence.py --host $hostPath --output (Join-Path $Evidence 'terms') --terms-url $TermsUrl --terms-sha256 $TermsSha256
    if ($LASTEXITCODE -ne 0) { throw 'Pinned official terms validation failed' }
    $termsReceipt = Join-Path $Evidence 'terms/terms-receipt.json'
    $receipt.termsReceipt = @{path='terms/terms-receipt.json'; sha256=(Get-FileHash -LiteralPath $termsReceipt -Algorithm SHA256).Hash.ToLowerInvariant()}
    $receipt.stage = 'compiler-probe'
    Save-Receipt
    $developer = Join-Path $instance.installationPath 'Common7\Tools\Launch-VsDevShell.ps1'
    if (-not (Test-Path -LiteralPath $developer -PathType Leaf)) { throw 'Installed developer shell missing' }
    & $developer -Arch amd64 -HostArch amd64 -SkipAutomaticLocation
    if ($env:VSCMD_VER -notmatch '^17\.') { throw 'VS2022 developer environment required' }
    $toolFiles = @(Get-Command cl.exe, link.exe, dumpbin.exe, cmake.exe | ForEach-Object {
        if ($_.Name -ne 'cmake.exe' -and -not $_.Source.StartsWith($instance.installationPath + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Compiler tools do not belong to selected installation'
        }
        @{name=$_.Name; path=$_.Source; sha256=(Get-FileHash -LiteralPath $_.Source -Algorithm SHA256).Hash.ToLowerInvariant()}
    })
    $receipt.toolchain = @{
        developerVersion=$env:VSCMD_VER; msvcVersion=$env:VCToolsVersion
        windowsSdkVersion=$env:WindowsSDKVersion
        tools=$toolFiles
    }
    $archivePaths = @('libcmt.lib', 'libcpmt.lib', 'libvcruntime.lib' | ForEach-Object {
        Join-Path $env:VCToolsInstallDir "lib/x64/$_"
    })
    $archivePaths += Join-Path $env:WindowsSdkDir "Lib/$($env:WindowsSDKVersion.TrimEnd('\'))/ucrt/x64/libucrt.lib"
    $receipt.availableStaticArchives = @($archivePaths | ForEach-Object {
        if (-not (Test-Path -LiteralPath $_ -PathType Leaf)) { throw 'Expected release static archive missing' }
        @{path=$_; sha256=(Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()}
    })
    # Available archive hashes are not a claim about final linker selection.
    $receipt.finalProductLinkAudit = 'not-assessed'
    # Fixed local probe: /Bv identifies compiler passes; /MT is not a final-product link audit.
    $probe = Join-Path $Evidence 'compiler-probe.c'
    $object = Join-Path $Evidence 'compiler-probe.obj'
    $log = Join-Path $Evidence 'compiler-version.log'
    'int document_files_compiler_probe(void) { return 0; }' | Set-Content -LiteralPath $probe -Encoding ascii
    & cl.exe /nologo /Bv /MT /c $probe "/Fo$object" 2>&1 | Out-File -LiteralPath $log -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'Compiler identity probe failed' }
    $receipt.compilerProbe = @{path='compiler-version.log'; sha256=(Get-FileHash -LiteralPath $log -Algorithm SHA256).Hash.ToLowerInvariant(); finalProductLinkAudit=$false}
    Remove-Item -LiteralPath $object -ErrorAction Stop
    $receipt.stage = 'candidate-build'
    Save-Receipt
    $license = Join-Path $Evidence 'terms/vs2022-enterprise-text.txt'
    # The supervisor assigns the suspended builder to one kill-on-close Job Object.
    # A CI timeout or supervisor exit alone is never evidence of successful cleanup.
    $buildArguments = @('scripts/windows_build_evidence.py', 'build', '--kind', $Kind,
        '--python', $Python, '--evidence', $Evidence, '--work', $Work, '--output', $Output,
        '--license', $license, '--commit', $receipt.sourceCommit,
        '--run-id', $receipt.executionRunId, '--timeout', "$BuildTimeoutSeconds")
    if ($Kind -eq 'cpu') { $buildArguments += @('--version', $PackVersion) }
    else { $buildArguments += @('--cache', $Cache) }
    & $Python @buildArguments
    $receipt.supervisorExitCode = $LASTEXITCODE
    $executionPath = Join-Path $Evidence 'build-execution.json'
    if (Test-Path -LiteralPath $executionPath -PathType Leaf) {
        $execution = Get-Content -LiteralPath $executionPath -Raw | ConvertFrom-Json
        if ($execution.executionRunId -ne $receipt.executionRunId) { throw 'Build execution identity mismatch' }
        $receipt.buildExitCode = $execution.exitCode
    }
    if ($receipt.supervisorExitCode -ne 0) { throw 'Candidate build failed' }
    $resultPath = Join-Path $Evidence 'build-result.json'
    $buildResult = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
    if ($buildResult.executionRunId -ne $receipt.executionRunId -or
        $buildResult.sourceCommit -ne $receipt.sourceCommit -or
        $buildResult.status -ne 'built-unreviewed' -or $null -eq $buildResult.outputs) {
        throw 'Candidate artifacts not linked to this execution'
    }
    $receipt.buildResult = @{path='build-result.json'; sha256=(Get-FileHash -LiteralPath $resultPath -Algorithm SHA256).Hash.ToLowerInvariant()}
    $receipt.outputs = $buildResult.outputs
    $receipt.status = 'built-unreviewed'
} catch {
    $receipt.status = 'failed'
    # Fixed stage is sufficient to locate diagnostic files; do not dump environment/commands.
    $receipt.failureCode = "windows-$($receipt.stage)-failed"
    throw
} finally {
    # Preserve partial supervisor records on interruption/failure without promoting them.
    foreach ($name in @('build-execution.json', 'build-result.json')) {
        $path = Join-Path $Evidence $name
        try {
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                $receipt[$name] = @{path=$name; sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()}
            }
        } catch { $receipt.evidenceLinkFailure=$true; $receipt.status='failed' }
    }
    $receipt.endedAt = [DateTime]::UtcNow.ToString('o')
    Save-Receipt
}

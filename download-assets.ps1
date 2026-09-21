param(
    [string]$Root = $PSScriptRoot,
    [ValidateSet('auto', 'windows', 'linux', 'macos', 'all')]
    [string]$Platform = 'auto',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$upstreamCommit = '44a1e116c6242d6ddc0b840bb1af405a5c68b41f'
$rawBase = "https://raw.githubusercontent.com/YaoFANGUK/video-subtitle-extractor/$upstreamCommit"

if ($Platform -eq 'auto') {
    if ($env:OS -eq 'Windows_NT') {
        $Platform = 'windows'
    } elseif ((uname -s) -eq 'Darwin') {
        $Platform = 'macos'
    } else {
        $Platform = 'linux'
    }
}

$assets = @(
    @{ path = 'backend/models/V5/arabic_PP-OCRv5_mobile_rec_infer/inference.pdiparams'; size = 7922839; sha256 = '4b4271fd1dd89a40b2056e1a42e58de7c0df2fdcf1a97ca9a8916d7ec45f9143'; platform = 'common' }
    @{ path = 'backend/models/V5/cyrillic_PP-OCRv5_mobile_rec_infer/inference.pdiparams'; size = 7972691; sha256 = '434dc9fa2a99fa3653e08f8cf793ae56be7dd41c35c4980e6255147cc02bbc80'; platform = 'common' }
    @{ path = 'backend/models/V5/devanagari_PP-OCRv5_mobile_rec_infer/inference.pdiparams'; size = 7836203; sha256 = '719be7d20bfe9530e2deae324c999e9911087496bce5e70846767c448d023a01'; platform = 'common' }
    @{ path = 'backend/models/V5/el_PP-OCRv5_mobile_rec_infer/inference.pdiparams'; size = 7732627; sha256 = '4d69bbb8ed9f84373631d121ab459f8583cd978df813b5fb9b139b7783b05fbd'; platform = 'common' }
    @{ path = 'backend/models/V5/eslav_PP-OCRv5_mobile_rec_infer/inference.pdiparams'; size = 7811519; sha256 = 'f11057b05d8517868bca505271278973d706600d9dcc184cbcf5c4512091c32b'; platform = 'common' }
    @{ path = 'backend/models/V5/korean_PP-OCRv5_mobile_rec_infer/inference.pdiparams'; size = 13342671; sha256 = 'cac3e5f12cf04aaa77f6a5bc704e4e736ef2908476551891d84b41b4e9090462'; platform = 'common' }
    @{ path = 'backend/models/V5/latin_PP-OCRv5_mobile_rec_infer/inference.pdiparams'; size = 7965915; sha256 = '53cdc8b481a7394bb108f96d0fb3432b0a8f392e22c7d18f06dbb2d42b8b25f9'; platform = 'common' }
    @{ path = 'backend/models/V5/PP-OCRv5_mobile_det_infer/inference.pdiparams'; size = 4692937; sha256 = 'afa1820cb16c1fd0dad589d0f8b389139061c1ef6d68019685fd07be997dda5b'; platform = 'common' }
    @{ path = 'backend/models/V5/PP-OCRv5_mobile_rec_infer/inference.pdiparams'; size = 16458665; sha256 = '2460da90875937c94db97eba74ae3d9e5d4c4c57c42f1f41531c09a26bcc771a'; platform = 'common' }
    @{ path = 'backend/models/V5/PP-OCRv5_server_det_infer/inference.pdiparams'; size = 87932887; sha256 = '183146fe9d9910352f68482f623bcbbb9fa7b9e8fa1463b9ad288cef00524d2d'; platform = 'common' }
    @{ path = 'backend/models/V5/PP-OCRv5_server_rec_infer/inference.pdiparams'; size = 84390117; sha256 = '63853f062a5f4089befc16f565a68277618e0da5cb45468b49d11079de0ada77'; platform = 'common' }
    @{ path = 'backend/models/V5/th_PP-OCRv5_mobile_rec_infer/inference.pdiparams'; size = 7814907; sha256 = '45ec91f2322b58b8d30ba27d18fcfdbb8bf388b918dd978162d2af91e0c66d4b'; platform = 'common' }
    @{ path = 'backend/subfinder/linux/VideoSubFinderCli'; size = 47121988; sha256 = '2d9e4bc170408eee05326094b4fc89f0c79b017d7a3ba2e767801c8adb2e85fa'; platform = 'linux' }
    @{ path = 'backend/subfinder/macos/VideoSubFinderCli'; size = 50996096; sha256 = '95c92dd9a62dcddf4d081bbe58821c645a3907cfbb060368dabf3f138a84338b'; platform = 'macos' }
    @{ path = 'backend/subfinder/windows/avcodec-58.dll'; size = 44948480; sha256 = 'a5d1c846e1750be61f93cd22beb633df13ac24e90eb9eda2d4f8a9ff7aeb6788'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/avdevice-58.dll'; size = 2752512; sha256 = 'c6d337dca4127de67997622fbaf18e837344b3aefd1a8fb390b0c72e4b7a882d'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/avfilter-7.dll'; size = 10862592; sha256 = '24e239d9ec5dd32e5295387991a13ac5f6d1d3a7d243654c0635b0adff68237b'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/avformat-58.dll'; size = 11337728; sha256 = 'b10f7b5d1ce6bf04766a18df805b0229110887cd982ef7951454df0b7b0b186d'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/avutil-56.dll'; size = 1057280; sha256 = '31baa999023e7c0183dd1c52285ab37003dd9c4cb034539aa958d365a571c0d0'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/concrt140.dll'; size = 315784; sha256 = '50d513a4e954b157b773f3419ef736c41529e782c3dc5bf2894d3be905b25183'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/cudart64_110.dll'; size = 401408; sha256 = '6fd9e73c8ba1c258dd6d109112eab78ac911e9862dc68081a7c9304b3170b9fc'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/msvcp140.dll'; size = 565640; sha256 = '256492fbbcf3dc63987318f83e5f49eaacdbb6a9a5be54e403f0ddb437582881'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/nppc64_11.dll'; size = 232448; sha256 = 'ec5d298b7e136cd30e854d82f331df393f93a4a2ae74060a704ed3a99831a21c'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/nppicc64_11.dll'; size = 4529152; sha256 = '2ea8f6fcd937d2aed63025fa7546fa8e2d9b3575e0d133e66485c56d1d76a897'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/nppig64_11.dll'; size = 26118656; sha256 = '8543419d9d8d3fba94db4181031c26eec3303d1e3e1b95b4724e41074b5c8ddc'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/opencv_videoio_ffmpeg430_64.dll'; size = 22099456; sha256 = 'c6207a8f7f0c84d68b955e73f906a847f56502ac813e48209fab7b455a8b7847'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/opencv_world430.dll'; size = 58043904; sha256 = '95634fd0b1a7ac3ba4f9e3498361f7f8b2502ebc17f7e4acc0dc51fd44fb767e'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/postproc-55.dll'; size = 135168; sha256 = 'a173cadfd95eb504933be4d756ee13fcac39217f4325be85e2e9b44979ca0c8c'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/swresample-3.dll'; size = 428544; sha256 = '9f285a414b7b5c8b5d54c00be36982665593278312a2b699ba5d38d5e17f0fef'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/swscale-5.dll'; size = 556544; sha256 = 'd39d72633460fa9e4842e319491c17e51c9c48840491a543f2c6050e2d5d93e7'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/vcruntime140.dll'; size = 109392; sha256 = 'a8f950b4357ec12cfccddc9094cca56a3d5244b95e09ea6e9a746489f2d58736'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/vcruntime140_1.dll'; size = 49520; sha256 = 'e4b533a94e02c574780e4b333fcf0889f65ed00d39e32c0fbbda2116f185873f'; platform = 'windows' }
    @{ path = 'backend/subfinder/windows/VideoSubFinderWXW.exe'; size = 6285824; sha256 = 'c6de741c2a1742e2bbf91ad6970bcf886fe26eaa725feb611cc43423c2bd9922'; platform = 'windows' }
    @{ path = 'backend/tools/NotoSansCJK-Bold.otf'; size = 17111432; sha256 = 'fd2fd3c84008b16fda09233e254ac0602e56b1f6f8b2a6e8b5049c5a19017946'; platform = 'common' }
)

$selected = $assets | Where-Object {
    $_.platform -eq 'common' -or $Platform -eq 'all' -or $_.platform -eq $Platform
}

Write-Host "Checking $($selected.Count) runtime assets for $Platform..."
$position = 0
foreach ($asset in $selected) {
    $position++
    $destination = Join-Path $Root ($asset.path.Replace('/', [IO.Path]::DirectorySeparatorChar))
    $valid = $false
    if ((-not $Force) -and (Test-Path -LiteralPath $destination -PathType Leaf)) {
        $file = Get-Item -LiteralPath $destination
        if ($file.Length -eq $asset.size) {
            $hash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
            $valid = $hash -eq $asset.sha256
        }
    }
    if ($valid) {
        Write-Host "[$position/$($selected.Count)] OK $($asset.path)"
        continue
    }

    $parent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $temporary = "$destination.download"
    $escapedPath = (($asset.path -split '/') | ForEach-Object { [uri]::EscapeDataString($_) }) -join '/'
    $url = "$rawBase/$escapedPath"
    Write-Host "[$position/$($selected.Count)] Downloading $($asset.path)"

    $downloaded = $false
    for ($attempt = 1; $attempt -le 3 -and -not $downloaded; $attempt++) {
        try {
            Invoke-WebRequest -Uri $url -OutFile $temporary -UseBasicParsing
            $file = Get-Item -LiteralPath $temporary
            $hash = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($file.Length -ne $asset.size -or $hash -ne $asset.sha256) {
                throw "Checksum verification failed for $($asset.path)."
            }
            Move-Item -LiteralPath $temporary -Destination $destination -Force
            $downloaded = $true
        } catch {
            if (Test-Path -LiteralPath $temporary) {
                Remove-Item -LiteralPath $temporary -Force
            }
            if ($attempt -eq 3) { throw }
            Write-Warning "Download attempt $attempt failed; retrying $($asset.path)."
        }
    }
}

if ($Platform -eq 'linux' -or $Platform -eq 'macos' -or $Platform -eq 'all') {
    $nativeTools = @(
        (Join-Path $Root 'backend/subfinder/linux/VideoSubFinderCli'),
        (Join-Path $Root 'backend/subfinder/macos/VideoSubFinderCli')
    ) | Where-Object { Test-Path -LiteralPath $_ }
    foreach ($tool in $nativeTools) { & chmod +x -- $tool }
}

Write-Host 'Runtime assets are ready.' -ForegroundColor Green

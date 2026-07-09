$content = Get-Content "D:\github_projects\resumeai\admin.html" -Raw

# Заменить старую функцию improveResume на новую с кнопкой скачивания
$old = '// Improve resume function
let lastResumeFile = null;

async function improveResume() {
  const btn = document.getElementById("improveBtn");
  btn.textContent = "⏳ Improving...";
  btn.disabled = true;
  document.getElementById("improveBox").style.display = "none";

  try {
    const fileInput = document.getElementById("fileInput");
    if (!fileInput.files[0]) {
      alert("Please select a resume file first");
      return;
    }
    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    const response = await fetch("/api/admin/improve", {
      method: "POST",
      body: formData
    });

    const data = await response.json();
    if (data.success) {
      document.getElementById("improveContent").textContent = data.improved_resume;
      document.getElementById("improveBox").style.display = "block";
      document.getElementById("improveBox").scrollIntoView({behavior: "smooth"});
    } else {
      alert("Error: " + (data.error || "Unknown error"));
    }
  } catch(e) {
    alert("Error: " + e.message);
  } finally {
    btn.textContent = "✨ Improve Resume (Admin)";
    btn.disabled = false;
  }
}'

$new = '// Improve resume function
let improvedResumeText = null;

async function improveResume() {
  const btn = document.getElementById("improveBtn");
  btn.textContent = "⏳ Improving...";
  btn.disabled = true;
  document.getElementById("improveBox").style.display = "none";
  improvedResumeText = null;

  try {
    const fileInput = document.getElementById("fileInput");
    if (!fileInput.files[0]) { alert("Please select a resume file first"); return; }
    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    const response = await fetch("/api/admin/improve", { method: "POST", body: formData });
    const data = await response.json();

    if (data.success) {
      improvedResumeText = data.improved_resume;
      document.getElementById("improveContent").textContent = data.improved_resume;
      document.getElementById("improveBox").style.display = "block";
      document.getElementById("downloadDocxBtn").style.display = "block";
      const lang = data.detected_language || "original";
      document.getElementById("improveBox").querySelector(".result-title").textContent =
        "Improved Resume (Language: " + lang + ")";
      document.getElementById("improveBox").scrollIntoView({behavior: "smooth"});
    } else {
      alert("Error: " + (data.error || "Unknown error"));
    }
  } catch(e) { alert("Error: " + e.message); }
  finally {
    btn.textContent = "✨ Improve Resume (Admin)";
    btn.disabled = false;
  }
}

async function downloadDocx() {
  if (!improvedResumeText) { alert("No improved resume to download"); return; }
  const btn = document.getElementById("downloadDocxBtn");
  btn.textContent = "⏳ Generating DOCX...";
  btn.disabled = true;
  try {
    const response = await fetch("/api/admin/improve/docx", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({improved_resume: improvedResumeText})
    });
    if (response.ok) {
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "improved_resume.docx"; a.click();
      URL.revokeObjectURL(url);
    } else {
      alert("Error generating DOCX");
    }
  } catch(e) { alert("Error: " + e.message); }
  finally { btn.textContent = "📥 Download as DOCX"; btn.disabled = false; }
}'

$content = $content.Replace($old, $new)

# Добавить кнопку скачивания DOCX в improveBox
$oldBox = '  <div class="result-box" id="improveBox" style="display:none;margin-top:1rem">
    <div class="result-header">
      <div class="result-title">Improved Resume</div>
      <div class="admin-badge-result">ADMIN MODE</div>
    </div>
    <div class="result-content" id="improveContent" style="white-space:pre-wrap;font-family:inherit"></div>
  </div>'

$newBox = '  <div class="result-box" id="improveBox" style="display:none;margin-top:1rem">
    <div class="result-header">
      <div class="result-title">Improved Resume</div>
      <div class="admin-badge-result">ADMIN MODE</div>
    </div>
    <div class="result-content" id="improveContent" style="white-space:pre-wrap;font-family:inherit"></div>
    <button class="btn" id="downloadDocxBtn" onclick="downloadDocx()" style="display:none;margin-top:1rem;width:100%;background:#2ecc71">
      📥 Download as DOCX
    </button>
  </div>'

$content = $content.Replace($oldBox, $newBox)
Set-Content "D:\github_projects\resumeai\admin.html" $content -Encoding UTF8
Write-Host "Done!"

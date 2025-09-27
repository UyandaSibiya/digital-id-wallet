// Utility functions
function $(id) { return document.getElementById(id); }
function show(el) { el.style.display = ''; }
function hide(el) { el.style.display = 'none'; }

// Toggle forms
function showLoginForm() {
    show($('login-form'));
    hide($('register-form'));
    $('form-title').textContent = 'Access Your Digital Wallet';
}
function showRegisterForm() {
    hide($('login-form'));
    show($('register-form'));
    $('form-title').textContent = 'Create Your Account';
}

// Keyboard accessibility for toggle links
$('show-register').addEventListener('click', showRegisterForm);
$('show-login').addEventListener('click', showLoginForm);
$('show-register').addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') showRegisterForm(); });
$('show-login').addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') showLoginForm(); });

// Simple email validation (optional)
function isValidEmail(email) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

// Camera stream
const video = document.getElementById('video');
if (video) {
  navigator.mediaDevices.getUserMedia({ video: true })
    .then(stream => { video.srcObject = stream; })
    .catch(err => console.error("Camera error:", err));
}

const canvas = document.getElementById('canvas');
const faceForm = document.getElementById('faceForm');
const faceInput = document.getElementById('faceInput');

document.getElementById('faceLoginBtn')?.addEventListener('click', () => {
  const context = canvas.getContext('2d');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  context.drawImage(video, 0, 0, canvas.width, canvas.height);

  canvas.toBlob(blob => {
    const file = new File([blob], "face_login.png", { type: "image/png" });

    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);
    faceInput.files = dataTransfer.files;

    faceForm.submit();
  }, "image/png");


  // ---------------- AI Chat ----------------
const toggleBtn = document.getElementById('robotToggle');
const dashboard = document.getElementById('aiDashboard');
const aiBody = document.getElementById('aiBody');
const aiInput = document.getElementById('aiInput');
const aiSend = document.getElementById('aiSend');

toggleBtn?.addEventListener('click', () => {
  dashboard.style.display = (dashboard.style.display === 'flex') ? 'none' : 'flex';
});

function addMessage(sender, text) {
  const p = document.createElement('p');
  p.className = sender === "AI" ? "ai" : "user";
  p.textContent = text;
  aiBody.appendChild(p);
  aiBody.scrollTop = aiBody.scrollHeight;
}

async function sendMessage() {
  const message = aiInput.value.trim();
  if (!message) return;
  addMessage("You", message);
  aiInput.value = "";

  try {
    const response = await fetch("/ask_ai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message })
    });
    const data = await response.json();
    addMessage("AI", data.reply);
  } catch (err) {
    addMessage("AI", "⚠️ Error sending message.");
  }
}

aiSend?.addEventListener('click', sendMessage);
aiInput?.addEventListener('keypress', (e) => {
  if (e.key === 'Enter') sendMessage();
});
});

// Initial state
showLoginForm();
hide($('aiDashboard')); // Hide AI chat by default

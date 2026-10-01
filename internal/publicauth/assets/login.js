(() => {
  const form = document.getElementById("owner-login");
  const username = document.getElementById("owner-username");
  const password = document.getElementById("owner-password");
  const error = document.getElementById("login-error");
  const submit = document.getElementById("login-submit");
  if (!form || !username || !password || !error || !submit) return;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hidden = true;
    submit.disabled = true;
    submit.querySelector("span").textContent = "Проверяем…";
    try {
      const response = await fetch("/phone/api/v1/public-auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.value, password: password.value }),
      });
      if (!response.ok) throw new Error("login_failed");
      password.value = "";
      window.location.assign("/phone/");
    } catch {
      password.value = "";
      error.hidden = false;
      password.focus();
    } finally {
      submit.disabled = false;
      submit.querySelector("span").textContent = "Продолжить";
    }
  });
})();

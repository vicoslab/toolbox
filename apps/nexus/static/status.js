const el = document.getElementById("status");
const check = async () => {
    let status = await fetch("/task/status").then(x => x.text());
    el.className = status;
    el.innerText = status.substring(0, 1).toUpperCase() + status.substring(1);
};
check();
setInterval(check, 5000);
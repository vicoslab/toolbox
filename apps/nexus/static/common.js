const toasts = document.getElementById("toast-wrapper");
const toastTemplate = document.getElementById("toast");
function showToast(category, ...content) {
    const title = document.createElement("span");
    title.className = "toast-title";

    const toast = document.createElement("div");
    toast.style.backgroundColor = "var(--bg-primary)";
    toast.className = "toast";

    switch (category) {
        case "info": {
            title.innerText = "Info";
            toast.classList.add(category);
            break;
        }
        case "error": {
            title.innerText = "Error";
            toast.classList.add(category);
            break;
        }
    }

    toast.append(title, ...content);
    toasts.append(toast);
    setTimeout(() => toast.classList.add("animate"), 1);
    return new Promise((resolve) => {
        setTimeout(async () => {
            await resolve(toast);
            if (toast.keepAlive === true) {
                const close = document.createElement("button");
                close.innerHTML = `<img style="height: 1.2rem" src="/static/icons/remove.svg">`;
                close.addEventListener("click", () => toast.remove());
                close.style = "position: absolute; top: 0.5rem; right: 0.5rem; padding: 0;";
                close.className = "transparent shadow-sm";
                toast.append(close);
            } else {
                toast.remove();
            }
        }, 5000);
    });
}

function loading({ message }) {
    const wrapper = document.createElement("div");
    wrapper.className = "loading";
    wrapper.popover = "";
    wrapper.innerHTML = `<svg viewBox="0 0 24 24" fill="transparent" stroke="currentColor" stroke-width="2px" stroke-linecap="round"><path d="M 12 19 A 1 1 0 0 0 12 5 A 1 1 0 0 0 12 19"></path></svg>`;
    wrapper.append(message);
    document.body.append(wrapper);
    wrapper.showPopover();
    return wrapper;
}

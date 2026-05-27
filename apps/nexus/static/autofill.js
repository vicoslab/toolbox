function autofillSetup(model) {
    const key = `toolbox-model-${model}`;
    const saved = JSON.parse(localStorage.getItem(key) || "{}");

    const form = document.getElementsByTagName("form")[0];
    const inputs = form.getElementsByTagName("input");
    for (const input of inputs) {
        if (input.name in saved) {
            if (input.type === "checkbox") {
                input.checked = saved[input.name];
            } else if (input.type == "radio") {
                if (saved[input.name] === input.value) {
                    input.checked = true;
                }
            } else {
                input.value = saved[input.name];
            }
        }
    }
    form.addEventListener("submit", e => {
        // need to refetch this every time
        const saved = JSON.parse(localStorage.getItem(key) || "{}");
        const updated = {};
        for (const input of inputs) {
            if (input.type === "checkbox") {
                updated[input.name] = input.checked;
            } else if (input.type === "radio") {
                if (input.checked) {
                    updated[input.name] = input.value;
                }
            } else if (input.type !== "submit" && (input.value || input.name in saved)) {
                updated[input.name] = input.value;
            }
        }
        localStorage.setItem(key, JSON.stringify(updated));
    });
    console.log(`Initialised autofill for ${model}`);
}

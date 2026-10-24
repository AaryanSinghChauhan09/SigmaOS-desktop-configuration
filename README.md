    # SigmaOS-desktop-configuration

    Desktop flavour configuration (browser, shell, WM)

    Part of the **SigmaOS** sovereign operating-system ecosystem — a multi-repository
    distribution modelled on Linux Mint and Omarchy Linux. See the parent
    [`SigmaOS`](https://github.com/AaryanSinghChauhan09/SigmaOS) repository for the
    full component index.

    ## Upstream inspirations
    - https://github.com/linuxmint/mintdesktop

    ## Design principles applied
    - **Declarative over Imperative**
- **Plan Before Apply**
- **Fail-Fast**
- **KISS**
- **Deterministic Testing**

    ## Usage
    Verbs: `get, set, list, apply-plan, profile`

    ```
    $ sigma-configurator get --file system.conf --key appearance.theme
$ sigma-configurator set --file system.conf --key appearance.theme --value "Adwaita-dark"
$ sigma-configurator list --file system.conf
$ sigma-configurator apply-plan --file system.conf --desired desired.json --schema schema.json
$ sigma-configurator profile --file system.conf --profile base=laptop.json --profile work=work.json

    ```

    ## Build & test
    ```
    make build
    make test
    ```

    ## Licence
    GNU General Public License v3.0 (FOSS).
